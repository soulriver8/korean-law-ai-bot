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

    # ✅ STEP 1: 검색어 최적화 (할루시네이션 방지 모드)
    optimizer_prompt = f"""사용자의 질문: "{request.message}"
    너는 법률 검색어 최적화 AI야. 
    [초중요 경고] 너의 사전 지식으로 조문 번호(제O조)를 절대 유추하거나 지어내지 마! 법령은 계속 개정되므로 너의 기억은 틀릴 수 있어.
    사용자가 질문에서 "제O조"를 직접 언급하지 않았다면, 무조건 '정확한 법령명'과 '핵심 명사 키워드' 조합으로만 검색어를 만들어.
    반드시 1줄의 검색어만 출력할 것.
    
    예시 1: "민방위 면제는 몇살부터?" -> "민방위기본법 편성 면제 연령" (O) / "민방위기본법 제18조" (X - 조항 지어내기 금지!)
    예시 2: "근로기준법 60조 연차 규정" -> "근로기준법 제60조 연차" (O - 사용자가 직접 언급했으므로 허용)
    예시 3: "월세 중도 해지" -> "주택임대차보호법 계약 해지" (O)
    """
    try:
        opt_resp = gemini_client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=optimizer_prompt,
            config=genai_types.GenerateContentConfig(temperature=0.1)
        )
        optimized_query = opt_resp.text.strip()
    except Exception as e:
        print(f"⚠️ 검색어 최적화 실패: {e}")
        optimized_query = request.message

    print(f"🎯 정제된 키워드: {optimized_query}")

    # ✅ STEP 2: MCP 호출 (최적화된 검색어 사용)
    search_result_prompt = ""
    search_result_data = ""

    async with httpx.AsyncClient() as client:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "legal_qa_tool",
                    "arguments": {"query": optimized_query}  # ✅ 최적화된 쿼리
                }
            }
            resp = await client.post(MCP_URL, json=payload, timeout=60.0)
            resp.raise_for_status()

            for line in resp.text.splitlines():
                if line.startswith("data: "):
                    try:
                        parsed_data = json.loads(line[6:])
                        content_list = parsed_data.get("result", {}).get("content", [])
                        for item in content_list:
                            text = item.get("text", "")
                            if text.startswith("{") and '"success"' in text:
                                search_result_data = text
                            else:
                                search_result_prompt = text
                        break
                    except json.JSONDecodeError:
                        continue

        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="법령 검색 서버 응답 지연")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"MCP 호출 실패: {str(e)}")

    # ✅ STEP 3: Gemini 최종 답변 (MCP 지시문 활용)
    contents = []
    for msg in request.history:
        contents.append(
            genai_types.Content(
                role=msg.role,
                parts=[genai_types.Part.from_text(text=msg.content)]
            )
        )

    final_prompt = f"""사용자 질문: {request.message}

{search_result_prompt}

[법령 데이터]
{search_result_data}
"""
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
        
        # ✨ [수정됨] 프론트엔드로 디버깅 데이터를 함께 넘겨줍니다.
        return {
            "answer": final_response.text,
            "debug_info": {
                "original_msg": request.message,
                "optimized_query": optimized_query,
                "raw_mcp_data": search_result_data if search_result_data else search_result_prompt
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM 처리 중 오류: {str(e)}")