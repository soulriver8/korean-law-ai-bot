import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google import genai
from google.genai import types as genai_types
import json
from typing import List

# 1. 🛡️ 데이터 검증 모델(Pydantic) 확장
class ChatMessage(BaseModel):
    role: str  # "user" (사용자) 또는 "model" (AI)
    content: str

class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []  # 과거 대화 내역 (기본값: 빈 배열)

# (주의: 중간에 중복 선언되어 있던 낡은 ChatRequest 클래스는 삭제했습니다!)

# 2. 환경 변수 및 Gemini 초기화
env_path = os.path.join(os.getcwd(), "lexguard-mcp", ".env")
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise RuntimeError("❌ [System] GOOGLE_API_KEY 누락")

gemini_client = genai.Client(api_key=api_key)
sys_instruct = "너는 대한민국 법령 전문 인공지능 'LexGuard'이다. 제공된 법령 검색 결과를 바탕으로 답변하고, 출처와 조항 번호를 반드시 인용하라."
gemini_config = genai_types.GenerateContentConfig(system_instruction=sys_instruct, temperature=0.1)

# 3. FastAPI 앱 및 라우터 초기화
app = FastAPI()

MCP_URL = os.getenv("MCP_INTERNAL_URL", "http://localhost:8099/mcp")

@app.get("/")
async def serve_frontend():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>index.html 파일을 찾을 수 없습니다.</h1>", status_code=404)

# ✨ 헬스체크 라우터를 메인 실행 블록 위로 끌어올렸습니다.
@app.get("/health")
async def health_check():
    return {"status": "alive", "target": "Law Chatbot Main"}

@app.post("/api/chat")
async def chat(request: ChatRequest):
    search_result = "검색된 데이터가 없습니다."
    
    print(f"🚀 [Debug] 현재 연결 시도 중인 MCP 서버 주소: {MCP_URL}")

    async with httpx.AsyncClient() as client:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "legal_qa_tool", 
                    "arguments": {
                        "query": request.message 
                    }
                }
            }
            resp = await client.post(MCP_URL, json=payload, timeout=60.0)
            resp.raise_for_status()

            raw_text = resp.text
            parsed_data = None
            
            for line in raw_text.splitlines():
                if line.startswith("data: "):
                    try:
                        parsed_data = json.loads(line[6:])
                        break
                    except json.JSONDecodeError:
                        continue
            
            if parsed_data and "result" in parsed_data:
                content_list = parsed_data["result"].get("content", [])
                if content_list and len(content_list) > 0:
                    search_result = content_list[0].get("text", "검색 결과 텍스트가 없습니다.")
                else:
                    search_result = str(parsed_data["result"])
            else:
                search_result = "MCP 서버에서 올바른 형식의 응답을 받지 못했습니다."
                
        except httpx.TimeoutException:
            search_result = "법령 검색 서버 응답 지연 (Timeout)."
        except httpx.HTTPStatusError as e:
            print(f"⚠️ [Error] MCP 서버 상태 이상: {e.response.status_code}")
            search_result = f"법령 검색 서버가 현재 응답할 수 없습니다. (상태 코드: {e.response.status_code})"
        except Exception as e:
            print(f"⚠️ LexGuard 호출 실패: {e}")
            search_result = "법령 검색 서버에 연결할 수 없습니다."

    # ✨ [치명적 버그 수정] 빈 리스트를 먼저 선언해야 합니다!
    contents = []

    for msg in request.history:
        contents.append(
            genai_types.Content(
                role=msg.role, 
                parts=[genai_types.Part.from_text(text=msg.content)]
            )
        )

    final_prompt = f"사용자 질문: {request.message}\n\n[국가법령 검색 데이터]\n{search_result}\n\n위 데이터와 이전 대화 맥락에 근거하여 답변해."
    contents.append(
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=final_prompt)]
        )
    )
    
    try:
        final_response = gemini_client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=contents,
            config=gemini_config
        )
        return {"answer": final_response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM 처리 중 오류: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)