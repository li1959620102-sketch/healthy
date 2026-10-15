from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser, JsonOutputParser
from dotenv import load_dotenv
from typing import List, Optional, Dict, Any
import os
import json

# 加载环境变量
load_dotenv()

# 设置环境变量
os.environ["OPENAI_API_KEY"] = os.getenv("API_KEY")
os.environ["OPENAI_BASE_URL"] = os.getenv("BASE_URL")


# ============================================================
# 1. 定义 Pydantic 数据模型（用于 Pydantic 解析器）
# ============================================================
class KeywordResult(BaseModel):
	"""关键词提取结果模型"""
	text: str = Field(description="原始输入文本")
	keywords: List[str] = Field(description="提取的关键词列表")
	summary: Optional[str] = Field(default=None, description="文本的简短摘要")
	entities: Optional[List[Dict[str, str]]] = Field(
		default=None,
		description="识别的实体列表，如人名、邮箱、公司等"
	)


# ============================================================
# 2. 定义 KeywordExtractor 类
# ============================================================
class KeywordExtractor:
	"""
	关键词提取器，支持 Pydantic 和 JSON 两种解析器

	使用示例：
		extractor = KeywordExtractor(parser_type="pydantic")
		result = extractor.extract("张三，邮箱zhangsan@email.com，有5年工作经验")
		print(result)
	"""

	def __init__(self, parser_type: str = "pydantic", model_name: str = "deepseek-chat", temperature: float = 0.3):
		"""
		初始化关键词提取器

		Args:
			parser_type: 解析器类型，"pydantic" 或 "json"，默认 "pydantic"
			model_name: 使用的模型名称，默认 "deepseek-chat"
			temperature: 模型温度参数，默认 0.3（较低温度使输出更稳定）
		"""
		self.parser_type = parser_type
		self.chat = ChatOpenAI(
			model_name=model_name,
			temperature=temperature
		)

		# 根据类型初始化解析器
		if parser_type == "pydantic":
			self.parser = PydanticOutputParser(pydantic_object=KeywordResult)
		else:
			self.parser = JsonOutputParser()

	def _get_format_instructions(self) -> str:
		"""获取格式指令"""
		if self.parser_type == "pydantic":
			return self.parser.get_format_instructions()
		else:
			# JSON 解析器的格式指令
			return """
请以 JSON 格式返回结果，格式如下：
{
    "text": "原始输入文本",
    "keywords": ["关键词1", "关键词2", ...],
    "summary": "文本简短摘要（可选）",
    "entities": [
        {"type": "人名", "value": "张三"},
        {"type": "邮箱", "value": "zhangsan@email.com"}
    ]
}
"""

	def _build_prompt(self, query: str) -> str:
		"""构建提示词"""
		if self.parser_type == "pydantic":
			# Pydantic 解析器的模板（使用 format_instructions 变量）
			template = """
你是一个专业的关键词提取助手。请从以下文本中提取关键信息，并以指定的 JSON 格式返回。

{format_instructions}

文本内容：
{query}

请只返回 JSON 格式的结果，不要包含其他内容。
"""
			prompt = PromptTemplate(
				template=template,
				partial_variables={"format_instructions": self._get_format_instructions()}
			)
			return prompt.format(query=query)
		else:
			# JSON 解析器的模板（直接填充格式指令）
			template = """
你是一个专业的关键词提取助手。请从以下文本中提取关键信息，并以指定的 JSON 格式返回。

格式要求：
{format_instructions}

文本内容：
{query}

请只返回 JSON 格式的结果，不要包含其他内容。
"""
			prompt = PromptTemplate(
				template=template
			)
			return prompt.format(
				query=query,
				format_instructions=self._get_format_instructions()
			)

	def extract(self, query: str) -> KeywordResult | Dict[str, Any]:
		"""
		提取关键词

		Args:
			query: 待提取的文本

		Returns:
			Pydantic 模式下返回 KeywordResult 对象
			JSON 模式下返回 dict
		"""
		# 构建提示词
		prompt = self._build_prompt(query)

		# 调用模型
		response = self.chat.invoke(prompt)

		# 解析输出
		try:
			if self.parser_type == "pydantic":
				result = self.parser.invoke(response)
			else:
				# JSON 解析器需要解析 response.content
				result = json.loads(response.content)
			return result
		except Exception as e:
			print(f"解析失败: {e}")
			print(f"原始输出: {response.content}")
			# 返回一个默认结构
			if self.parser_type == "pydantic":
				return KeywordResult(
					text=query,
					keywords=["解析失败"],
					summary="请检查模型输出格式"
				)
			else:
				return {
					"text": query,
					"keywords": ["解析失败"],
					"summary": "请检查模型输出格式",
					"entities": []
				}

	def extract_batch(self, texts: List[str]) -> List[KeywordResult | Dict[str, Any]]:
		"""
		批量提取关键词

		Args:
			texts: 文本列表

		Returns:
			结果列表
		"""
		results = []
		for text in texts:
			result = self.extract(text)
			results.append(result)
		return results

	def to_json(self, result: KeywordResult | Dict[str, Any]) -> str:
		"""将结果转换为 JSON 字符串"""
		if hasattr(result, 'model_dump'):
			return result.model_dump_json(indent=2)
		elif hasattr(result, 'dict'):
			return json.dumps(result.dict(), ensure_ascii=False, indent=2)
		else:
			return json.dumps(result, ensure_ascii=False, indent=2)


# ============================================================
# 3. 测试代码
# ============================================================
if __name__ == "__main__":
	# 测试用例
	test_texts = [
		"张三，邮箱zhangsan@email.com，有5年工作经验，擅长Python和Java，之前在ABC公司工作",
		"李四的联系方式是lisi@example.com，工作经验3年，技能包括JavaScript、React，上一家公司是XYZ科技",
		"王五，工作8年，会项目管理和团队协作，邮箱wangwu@test.com，之前在DEF集团"
	]
	query2 = "《朝花夕拾》原名《旧事重提》，是现代文学家鲁迅的散文集，收录鲁迅于1926年创作的10篇回忆性散文。"
	print("=" * 70)
	print("测试 Pydantic 解析器")
	print("=" * 70)

	# 创建 Pydantic 解析器
	extractor_pydantic = KeywordExtractor(parser_type="pydantic")

	for i, text in enumerate(test_texts, 1):
		print(f"\n--- 文本 {i} ---")
		print(f"原文: {text}")
		result = extractor_pydantic.extract(text)
		print(f"结果: {result}")
		print(f"关键词: {result.keywords}")
		if result.entities:
			print(f"实体: {result.entities}")

	print("\n" + "=" * 70)
	print("测试 JSON 解析器")
	print("=" * 70)

	# 创建 JSON 解析器
	extractor_json = KeywordExtractor(parser_type="json")

	for i, text in enumerate(test_texts, 1):
		print(f"\n--- 文本 {i} ---")
		result = extractor_json.extract(text)
		print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}")