import os
import httpx
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

# 1. 모델 정의
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []

# 2. 초기화 (시스템 프롬프트 같은 복잡한 제약조건 다 날림!)
load_dotenv(dotenv_path=os.path.join(os.getcwd(), "lexguard-mcp", ".env"))
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise RuntimeError("❌ GOOGLE_API_KEY 누락")

gemini_client = genai.Client(api_key=api_key)
app = FastAPI()
MCP_URL = os.getenv("MCP_INTERNAL_URL", "http://localhost:8099/mcp")

@app.get("/")
async def serve_frontend():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>index.html 파일을 찾을 수 없습니다.</h1>", status_code=404)

@app.get("/health")
async def health_check():
    return {"status": "alive"}

@app.post("/api/chat")
async def chat(request: ChatRequest):
    print("\n" + "="*50)
    print(f"🚀 [초심 모드] 사용자 질문 그대로 MCP 전송: {request.message}")
    
    # ✅ STEP 1: Agent 최적화 다 빼고, 원본 자연어 그대로 MCP 호출
    search_result_data = ""
    async with httpx.AsyncClient() as client:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "legal_qa_tool",
                    "arguments": {"query": request.message}  # 🚨 꼼수 없이 원본 질문 그대로 찌름!
                }
            }
            resp = await client.post(MCP_URL, json=payload, timeout=60.0)
            resp.raise_for_status()

            for line in resp.text.splitlines():
                if line.startswith("data: "):
                    try:
                        parsed_data = json.loads(line[6:])
                        content_list = parsed_data.get("result", {}).get("content", [])
                        if content_list:
                            # MCP가 주는 마지막 데이터 뭉치 획득
                            search_result_data = content_list[-1].get("text", "")
                        break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"⚠️ MCP 에러: {e}")
            search_result_data = f"데이터 검색 실패: {str(e)}"

    print(f"📦 MCP 반환 데이터 길이: {len(search_result_data)} 자")
    print("="*50 + "\n")

    # ✅ STEP 2: Gemini 최종 답변 (엄격한 포맷 지시 다 빼고, 그냥 대답하라고 함)
    contents = []
    for msg in request.history:
        contents.append(
            genai_types.Content(role=msg.role, parts=[genai_types.Part.from_text(text=msg.content)])
        )

    final_prompt = f"""사용자 질문: {request.message}

[MCP 서버가 가져온 데이터]
{search_result_data}
====================
위 데이터를 바탕으로 사용자 질문에 자유롭고 친절하게 답변해줘. (데이터에 내용이 부족해도 네가 아는 선에서 유연하게 대답해봐.)
"""
    contents.append(
        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=final_prompt)])
    )

    try:
        final_response = gemini_client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=contents,
        )
        return {
            "answer": final_response.text,
            "debug_info": {
                "original_msg": request.message,
                "optimized_query": "최적화 기능 꺼짐 (원본 그대로 전송됨)",
                "raw_mcp_data": search_result_data
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM 처리 중 오류: {str(e)}")