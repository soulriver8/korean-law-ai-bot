import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google import genai
from google.genai import types as genai_types
import json

# 1. 환경 변수 및 Gemini 초기화
env_path = os.path.join(os.getcwd(), "lexguard-mcp", ".env")
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise RuntimeError("❌ [System] GOOGLE_API_KEY 누락")

gemini_client = genai.Client(api_key=api_key)
sys_instruct = "너는 대한민국 법령 전문 인공지능 'LexGuard'이다. 제공된 법령 검색 결과를 바탕으로 답변하고, 출처와 조항 번호를 반드시 인용하라."
gemini_config = genai_types.GenerateContentConfig(system_instruction=sys_instruct, temperature=0.1)

# 2. 외부 의존성(lifespan)을 완벽히 제거. 서버는 무조건 즉시 켜집니다.
app = FastAPI()

# LexGuard MCP의 HTTP RPC 엔드포인트
MCP_URL = os.getenv("MCP_INTERNAL_URL", "http://localhost:8099/mcp")

class ChatRequest(BaseModel):
    message: str

@app.get("/")
async def serve_frontend():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>index.html 파일을 찾을 수 없습니다.</h1>", status_code=404)

@app.post("/api/chat")
async def chat(request: ChatRequest):
    # 3. Stateless 비동기 S2S 통신 (필요할 때만 가볍게 API 호출)
    search_result = "검색된 데이터가 없습니다."
    
    # ✨ 2. 도대체 어디로 요청을 보내는지 로그를 찍어봅니다. (디버깅용)
    print(f"🚀 [Debug] 현재 연결 시도 중인 MCP 서버 주소: {MCP_URL}")

    async with httpx.AsyncClient() as client:
        try:
            # JSON-RPC 표준 규격으로 검색 도구 호출
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    # ✨ 1. README에서 찾은 진짜 도구 이름으로 정확히 교체
                    "name": "legal_qa_tool", 
                    
                    "arguments": {
                        # 범용 QA 툴이므로 사용자의 질문 전체를 그대로 던집니다.
                        "query": request.message 
                    }
                }
            }
            # ✨ 2. [치명적 버그 방어] LexGuard의 무거운 통합 검색(법령+판례+해석)을 
            # 기다려주기 위해 타임아웃을 10초 -> 60초로 대폭 늘립니다.
            resp = await client.post(MCP_URL, json=payload, timeout=60.0)
            resp.raise_for_status()

            # ✨ [핵심 수정] SSE 스트림 텍스트 수동 파싱 로직
            raw_text = resp.text
            parsed_data = None
            
            # 텍스트를 한 줄씩 읽으면서 "data: "로 시작하는 진짜 JSON 알맹이를 찾습니다.
            for line in raw_text.splitlines():
                if line.startswith("data: "):
                    try:
                        # "data: " 이후의 문자열만 잘라내어 JSON 객체로 변환합니다.
                        parsed_data = json.loads(line[6:])
                        break
                    except json.JSONDecodeError:
                        continue
            
            if parsed_data and "result" in parsed_data:
                # MCP 프로토콜 표준에 따라 content 배열 안의 text를 추출합니다.
                content_list = parsed_data["result"].get("content", [])
                if content_list and len(content_list) > 0:
                    search_result = content_list[0].get("text", "검색 결과 텍스트가 없습니다.")
                else:
                    search_result = str(parsed_data["result"])
            else:
                search_result = "MCP 서버에서 올바른 형식의 응답을 받지 못했습니다."
                
        except httpx.TimeoutException:
            search_result = "법령 검색 서버 응답 지연 (Timeout)."
        # ✨ [아키텍트 방어막] 502, 404 등 상대 서버가 죽었을 때 앱 전체가 뻗는 것을 방지
        except httpx.HTTPStatusError as e:
            print(f"⚠️ [Error] MCP 서버 상태 이상: {e.response.status_code}")
            search_result = f"법령 검색 서버가 현재 응답할 수 없습니다. (상태 코드: {e.response.status_code})"
        except Exception as e:
            print(f"⚠️ LexGuard 호출 실패: {e}")
            search_result = "법령 검색 서버에 연결할 수 없습니다."

    # 4. 검색된 '팩트' 데이터를 Gemini에게 주입하여 답변 생성 (할루시네이션 원천 차단)
    final_prompt = f"사용자 질문: {request.message}\n\n[국가법령 검색 데이터]\n{search_result}\n\n위 데이터에만 근거하여 답변을 제공해."
    
    try:
        final_response = gemini_client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=final_prompt,
            config=gemini_config
        )
        return {"answer": final_response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM 처리 중 오류: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

@app.get("/health")
async def health_check():
    return {"status": "alive", "target": "Law Chatbot Main"}