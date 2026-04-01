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

sys_instruct = """너는 대한민국 최고의 법률 전문 AI 비서 'LexGuard'이다.
너의 임무는 제공된 법령 검색 데이터를 분석하여, 사용자에게 가장 정확하고 정돈된 법률 가이드를 제공하는 것이다.
반드시 다음 원칙을 엄격하게 지켜서 답변하라.

[절대 원칙]
1. 할루시네이션(환각) 원천 차단: 사용자의 질문에 대한 답은 오직 함께 제공된 [국가법령 검색 데이터] 안에서만 찾아라. 
2. 모르면 모른다고 할 것: 제공된 데이터에 관련 내용이 없다면, 절대 너의 기존 지식으로 지어내지 말고 "제공된 법령 검색 결과에서는 해당 내용에 대한 정확한 법적 근거를 찾을 수 없습니다."라고 단호하게 답변하라.
3. 객관적 톤 유지: 법적 판단을 확정 짓는 표현("합법입니다", "불법입니다")을 피하고, "법령에 따르면 ~할 수 있습니다", "~로 해석될 여지가 있습니다"와 같이 유보적이고 전문적인 톤을 유지하라.

[답변 포맷]
답변은 무조건 아래의 마크다운 형식을 그대로 사용하여 작성하라. 줄글로 길게 늘어쓰는 것을 절대 금지한다.

### 📌 핵심 요약
- (질문에 대한 1~2줄 이내의 명확하고 간결한 방향 제시)

### ⚖️ 법적 근거
- **(관련 법령/판례명 및 조항 번호)**: (해당 조항의 핵심 내용 요약)
  *(예: **근로기준법 제56조**: 연장근로에 대한 가산임금 지급 규정)*

### 📖 상세 적용 해석
- (제공된 법적 근거가 사용자의 질문 상황에 어떻게 적용되는지 논리적으로 설명)
- (검색된 데이터에 있는 내용만 사용하여 2~3개의 불릿 포인트로 작성)

### ⚠️ 실무 체크포인트
- (데이터 내에 존재하는 예외 조항이나, 주의해야 할 판단 기준 1~2가지)
- (추가로 확인이 필요한 사실관계 질문)
"""

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
                    # ✨ [수정] 무조건 배열의 '마지막' 요소를 가져오도록 [-1]로 변경합니다.
                    # 이렇게 하면 앞에 잔소리가 몇 개가 붙든, 항상 진짜 데이터(마지막 요소)를 안전하게 빼옵니다.
                    search_result = content_list[-1].get("text", "검색 결과 텍스트가 없습니다.")
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

    # ✨ [디버깅 블랙박스 장착] AI의 뇌로 들어가기 직전의 순수(Raw) 데이터 확인
    print("\n" + "🔥"*25)
    print("🚨 [DEBUG] LexGuard MCP가 긁어온 Raw 데이터 🚨")
    print("🔥"*25)
    # 터미널 창이 수만 자의 글로 도배되는 것을 막기 위해 1500자까지만 자르거나, 
    # 원본을 다 보고 싶으시면 그냥 print(search_result)를 쓰시면 됩니다.
    print(search_result + "\n... (중략) ...") 
    print("="*50 + "\n")

    # ✨ [치명적 버그 수정] 빈 리스트를 먼저 선언해야 합니다!
    contents = []

    for msg in request.history:
        contents.append(
            genai_types.Content(
                role=msg.role, 
                parts=[genai_types.Part.from_text(text=msg.content)]
            )
        )

    final_prompt = f"""사용자 질문: {request.message}

    =========================================
    [국가법령 검색 데이터] 시작
    {search_result}
    [국가법령 검색 데이터] 끝
    =========================================

    위 [국가법령 검색 데이터] 구간의 내용만을 엄격하게 분석하여, 시스템에 설정된 [답변 포맷]에 맞춰 체계적으로 답변해. 데이터에 없는 내용은 절대 지어내지 마."""
    
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