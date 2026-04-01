"""
응답 크기 제한 유틸리티
🚨 [상남자 디버깅 모드] 절단기 영구 정지! 모든 데이터를 원본 그대로 통과시킵니다.
"""
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("lexguard-mcp")

# MCP 규격: 최대 응답 크기 24KB (하지만 이제 무시함)
MAX_RESPONSE_SIZE = 24076  # bytes
RESERVE_SIZE = 500
TARGET_SIZE = MAX_RESPONSE_SIZE - RESERVE_SIZE


def truncate_response(result: Dict[str, Any], max_size: int = TARGET_SIZE) -> Dict[str, Any]:
    """
    🚨 [수술 완료] 24KB 절단기 영구 정지! 
    데이터가 100MB라도 자르지 않고 무조건 원본 100%를 그대로 반환합니다.
    """
    return result


def shrink_response_bytes(result: Dict[str, Any], max_bytes: int = MAX_RESPONSE_SIZE) -> Dict[str, Any]:
    """
    🚨 [수술 완료] 바이트 하드 제한 영구 정지!
    무조건 원본 100%를 그대로 반환합니다.
    """
    return result


# =====================================================================
# 아래는 기존의 헬퍼 함수들입니다. (이제 메인 함수에서 호출하지 않으므로 작동하지 않음)
# 다른 파일에서 import 에러가 나지 않도록 모양만 남겨둡니다.
# =====================================================================

def summarize_text(text: str, max_length: int) -> str:
    if not isinstance(text, str):
        return str(text)
    return text

def aggressive_truncate(result: Dict[str, Any], max_size: int) -> Dict[str, Any]:
    return result

def get_response_size(result: Dict[str, Any]) -> int:
    try:
        json_str = json.dumps(result, ensure_ascii=False)
        return len(json_str.encode('utf-8'))
    except Exception:
        return 0

def _sync_content_json(result: Dict[str, Any]) -> Dict[str, Any]:
    return result

def _reduce_structured_content(structured: Dict[str, Any]) -> Dict[str, Any]:
    return structured