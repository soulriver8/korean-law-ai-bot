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
    
    # =================================================================
    # ✨ [STEP 1: Agentic Query Optimizer] Gemini의 뇌를 빌려 검색어 정제
    # =================================================================
    optimizer_prompt = f"""사용자의 질문: "{request.message}"
    너는 대한민국 최고의 법률 검색어 최적화 AI야. 위 질문을 국가법령정보센터 검색 엔진이 가장 잘 찾을 수 있는 '정확한 법령명'과 '핵심 명사' 조합으로 변환해.
    출력은 반드시 검색어 키워드만 나와야 해. 부가 설명은 절대 하지 마.
    예시 1: "민방위 면제는 몇살부터?" -> "민방위기본법 편성 면제"
    예시 2: "프리랜서도 연차 받을 수 있나요?" -> "근로기준법 연차휴가 근로자"
    예시 3: "월세 계약 중간에 해지하고 싶어요" -> "주택임대차보호법 계약해지"
    예시 4: "음주운전 벌금 얼마야?" -> "도로교통법 음주운전 벌칙"
    """
    
    try:
        opt_resp = gemini_client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=optimizer_prompt,
            config=genai_types.GenerateContentConfig(temperature=0.1)
        )
        optimized_query = opt_resp.text.strip()
    except Exception as e:
        print(f"⚠️ 검색어 최적화 실패, 원본 질문 사용: {e}")
        optimized_query = request.message
        
    print("\n" + "🧠"*25)
    print(f"🤖 [Agent Brain] 사용자의 일상어 질문: {request.message}")
    print(f"🎯 [Agent Brain] LexGuard로 보낼 정제된 키워드: {optimized_query}")
    print("🧠"*25 + "\n")

    # =================================================================
    # ✨ [STEP 2: MCP 서버 호출] 정제된 검색어(optimized_query) 사용
    # =================================================================
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
                        "query": optimized_query  # <--- 🚨 기존 request.message에서 변경됨!
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
                
                # [0]은 MCP 자체 프롬프트 지시문 → 무시
                # [1]은 실제 법령 데이터 JSON
                raw_json_str = None
                for item in content_list:
                    text = item.get("text", "")
                    if text.startswith("{") and '"success"' in text:
                        raw_json_str = text
                        break
                
                if raw_json_str:
                    try:
                        mcp_data = json.loads(raw_json_str)
                        
                        # 검색 실패 케이스 처리
                        if not mcp_data.get("success_search") or not mcp_data.get("has_legal_basis"):
                            missing = mcp_data.get("missing_reason", "NO_MATCH")
                            search_result = f"관련 법령을 찾을 수 없습니다. (사유: {missing})"
                        else:
                            # 법령 데이터 추출 및 정제
                            extracted_parts = []
                            laws = mcp_data.get("results", {}).get("laws", [])
                            
                            for law in laws:
                                law_name = law.get("law_name", "")
                                detail_str = law.get("detail", "")
                                
                                # detail이 문자열이면 다시 파싱
                                if isinstance(detail_str, str):
                                    try:
                                        detail = json.loads(detail_str)
                                    except:
                                        detail = {"raw": detail_str}
                                else:
                                    detail = detail_str
                                
                                # 조문 본문만 추출 (개정문 제외)
                                articles = []
                                try:
                                    # 법령 구조에서 조문 탐색
                                    body = detail.get("법령", {})
                                    jo_list = body.get("조문", {}).get("조문단위", [])
                                    if isinstance(jo_list, dict):
                                        jo_list = [jo_list]
                                    
                                    for jo in jo_list:
                                        jo_num = jo.get("조문번호", "")
                                        jo_title = jo.get("조문제목", "")
                                        jo_content = jo.get("조문내용", "")
                                        
                                        # 연차/휴가 관련 조문만 필터 (전체가 너무 클 경우)
                                        combined = f"{jo_title}{jo_content}"
                                        articles.append(f"제{jo_num}조({jo_title}): {jo_content}")
                                    
                                except Exception as e:
                                    print(f"⚠️ 조문 파싱 실패: {e}")
                                    articles.append(str(detail)[:3000])
                                
                                extracted_parts.append(
                                    f"=== {law_name} ===\n" + "\n".join(articles[:20])  # 최대 20개 조문
                                )
                            
                            search_result = "\n\n".join(extracted_parts) if extracted_parts else "조문 데이터를 추출할 수 없습니다."
                            
                    except json.JSONDecodeError as e:
                        print(f"⚠️ MCP JSON 파싱 실패: {e}")
                        search_result = raw_json_str[:3000]
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
    print(search_result[:1500] + "\n... (중략) ...") 
    print("="*50 + "\n")

    # =================================================================
    # ✨ [STEP 3: 최종 답변 생성] 과거 대화 내역과 데이터를 조합하여 렌더링
    # =================================================================
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
    
    # 🚨 중복 append 방지 (단 1번만 추가됨)
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