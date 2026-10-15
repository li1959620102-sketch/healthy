# test_universal_client.py
import requests
import json
import logging
import os
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from urllib.parse import urljoin

load_dotenv()

DIFY_TIME_OUT = int(os.getenv("DIFY_TIME_OUT"))
class DifyUniversalClient:
    """通用 Dify 客户端，支持聊天应用和工作流应用"""

    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key or os.getenv("DIFY_API_KEY","app-pk4PMDFLl0WJpAwMWZASM88q")
        self.base_url = base_url or os.getenv("DIFY_BASE_URL", "http://120.79.169.112:18001")

        if not self.api_key:
            raise ValueError("Dify API密钥未提供")

        # 标准化 URL
        self.base_url = self._normalize_url(self.base_url)

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)

        # 检测应用类型
        self.app_type = self._detect_app_type()

    def _normalize_url(self, url: str) -> str:
        """标准化 URL"""
        if not url.startswith(('http://', 'https://')):
            url = f"http://{url}"
        return url.rstrip('/')

    def _detect_app_type(self) -> str:
        """检测应用类型：chat 或 workflow"""
        # 尝试获取应用信息
        try:
            response = requests.get(f"{self.base_url}/parameters", headers=self.headers, timeout=DIFY_TIME_OUT)
            if response.status_code == 200:
                data = response.json()
                # 根据响应特征判断应用类型
                if 'app_type' in data:
                    return data['app_type']
                # 其他判断逻辑...
        except:
            pass

        # 默认返回 chat，因为大多数应用是聊天类型
        return "chat"

    def call_application(self,
                         query: str,
                         inputs: Dict[str, Any] = None,
                         user: str = "user-123",
                         response_mode: str = "blocking",
                         conversation_id: Optional[str] = None,
                         app_type: str = None) -> Dict[str, Any]:
        """
        通用应用调用方法，自动判断应用类型

        Args:
            query: 用户查询
            inputs: 输入参数
            user: 用户ID
            response_mode: 响应模式
            conversation_id: 会话ID
            app_type: 强制指定应用类型 (auto|chat|workflow)
        """
        # 确定应用类型
        if app_type == "auto" or app_type is None:
            final_app_type = self.app_type
        else:
            final_app_type = app_type

        self.logger.info(f"🔍 调用{final_app_type.upper()}应用，问题: {query}")

        if final_app_type == "workflow":
            return self._call_workflow(query, inputs or {}, user, response_mode)
        else:
            return self._call_chat(query, inputs or {}, user, response_mode, conversation_id)

    def _call_chat(self,
                   query: str,
                   inputs: Dict[str, Any],
                   user: str,
                   response_mode: str,
                   conversation_id: Optional[str]) -> Dict[str, Any]:
        """调用聊天应用"""
        endpoint = "chat-messages"
        url = urljoin(self.base_url, f"/v1/{endpoint}")

        data = {
            "inputs": inputs,
            "query": query,
            "user": user,
            "response_mode": response_mode
        }

        if conversation_id:
            data["conversation_id"] = conversation_id

        self.logger.info(f"💬 调用聊天端点: {url}")
        return self._make_request(url, data)

    def _call_workflow(self,
                       query: str,
                       inputs: Dict[str, Any],
                       user: str,
                       response_mode: str) -> Dict[str, Any]:
        """调用工作流应用"""
        # 工作流通常使用固定的端点格式
        endpoint = "workflows/run"
        url = urljoin(self.base_url, f"/v1/{endpoint}")

        # 对于工作流，通常将查询作为输入参数
        workflow_inputs = inputs.copy()
        workflow_inputs["query"] = query

        data = {
            "inputs": workflow_inputs,
            "user": user,
            "response_mode": response_mode
        }

        self.logger.info(f"⚙️  调用工作流端点: {url}")
        return self._make_request(url, data)

    def _make_request(self, url: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """发送请求"""
        try:
            self.logger.debug(f"请求数据: {json.dumps(data, ensure_ascii=False)}")

            response = requests.post(url, json=data, headers=self.headers, timeout=DIFY_TIME_OUT)
            self.logger.debug(f"响应状态码: {response.status_code}")

            if response.status_code == 400:
                error_data = response.json()
                if error_data.get('code') == 'not_workflow_app':
                    self.logger.warning("⚠️  检测到聊天应用，尝试切换端点...")
                    # 自动切换到聊天端点
                    return self._handle_app_type_mismatch(data)

            response.raise_for_status()

            result = response.json()
            self.logger.info("✅ 请求成功")
            return {"status": "success", "data": result}

        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP错误：{e}"
            self.logger.error(error_msg)
            try:
                error_detail = response.json()
                return {"status": "error", "message": error_msg, "detail": error_detail}
            except:
                return {"status": "error", "message": error_msg, "detail": response.text}
        except Exception as e:
            error_msg = f"请求失败：{str(e)}"
            self.logger.error(error_msg)
            return {"status": "error", "message": error_msg}

    def _handle_app_type_mismatch(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理应用类型不匹配的情况"""
        # 提取查询内容
        query = data.get('inputs', {}).get('query') or data.get('query', '')

        # 重新构建聊天应用请求
        chat_data = {
            "inputs": {},
            "query": query,
            "user": data.get('user', 'user-123'),
            "response_mode": data.get('response_mode', 'blocking')
        }

        endpoint = "chat-messages"
        url = urljoin(self.base_url, f"/v1/{endpoint}")

        self.logger.info(f"🔄 自动切换到聊天端点: {url}")

        try:
            response = requests.post(url, json=chat_data, headers=self.headers, timeout=DIFY_TIME_OUT)
            response.raise_for_status()
            result = response.json()
            return {"status": "success", "data": result, "auto_switched": True}
        except Exception as e:
            return {"status": "error", "message": f"自动切换失败: {str(e)}"}

    def test_connection(self) -> Dict[str, Any]:
        """测试连接和应用类型"""
        try:
            # 测试参数端点
            url = urljoin(self.base_url, "/v1/parameters")
            response = requests.get(url, headers=self.headers, timeout=DIFY_TIME_OUT)

            if response.status_code == 200:
                data = response.json()
                return {
                    "status": "success",
                    "app_type": self.app_type,
                    "parameters": data
                }
            else:
                return {
                    "status": "error",
                    "message": f"连接测试失败，状态码: {response.status_code}"
                }

        except Exception as e:
            return {"status": "error", "message": f"连接测试异常: {str(e)}"}
