# 自定义异常
class RAGException(Exception):
    """RAG流程通用异常"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")