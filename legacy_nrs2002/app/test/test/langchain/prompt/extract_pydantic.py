# # Langchain的结构化输出
# # 定义你的Pydantic类
# from langchain_core.prompts import PromptTemplate
# # from langchain_community.chat_models import ChatOpenAI
# from langchain_openai import ChatOpenAI
# from datetime import date
# from pydantic import BaseModel, Field
# from langchain_core.output_parsers import PydanticOutputParser
# from dotenv import load_dotenv
# import os
# # 加载环境变量
# load_dotenv()
#  
# os.environ["OPENAI_API_KEY"] = os.getenv("API_KEY")  # DeepSeek的API密钥
# os.environ["OPENAI_BASE_URL"] = os.getenv("BASE_URL")  # DeepSeek基础URL
#  
# # 实例化DeepSeek模型（替代原OpenAI实例）
# chat = ChatOpenAI(
#     model_name="deepseek-chat",  # DeepSeek对话模型固定名称
#     temperature=0.7  # 可调整随机性
# )
#  
# class MyDate(BaseModel):
#     year: int = Field(description="年")
#     month: int = Field(description="月")
#     day: int = Field(description="日")
#  
#  
# parser = PydanticOutputParser(pydantic_object=MyDate)
#  
# template = """
#     提取用户输入中的日期
#     用户输入:
#     {query}.
#     Extract the desired information into JSON format from the following passage.
#     {format_instructions}
# """
# prompt = PromptTemplate(
#     template=template,
#     partial_variables={"format_instructions": parser.get_format_instructions()}
# )
# query = "2024年十二月23日天气晴..."
#  
# input_format = prompt.format(query=query)
# output = chat.invoke(input_format)
# print("原始输出：", output.content)
#  
# output2 = parser.invoke(output)
# print("结构化输出：", output2)
#  
# # 转换成Python中的日期类型
# res_date = date(output2.year, output2.month, output2.day)
# print("转换成Python中的日期类型：", res_date)


# # from langchain_community.chat_models import ChatOpenAI
# from langchain_openai import ChatOpenAI
# from langchain_core.prompts import ChatPromptTemplate
# from pydantic import BaseModel, Field
# from langchain_core.output_parsers import PydanticOutputParser
# from langchain.output_parsers import OutputFixingParser
# from dotenv import load_dotenv
# from typing import List, Optional
# import os
#
# # 加载环境变量
# load_dotenv()
#
# # 设置环境变量
# os.environ["OPENAI_API_KEY"] = os.getenv("API_KEY")
# os.environ["OPENAI_BASE_URL"] = os.getenv("BASE_URL")
#
# # 实例化DeepSeek模型
# chat = ChatOpenAI(
#     model_name="deepseek-chat",
#     temperature=0.7
# )
#
#
# # 定义简历数据模型
# class Resume(BaseModel):
#     name: str = Field(description="姓名")
#     email: str = Field(description="邮箱")
#     experience_years: int = Field(description="工作年限")
#     skills: List[str] = Field(description="技能列表")
#     last_company: Optional[str] = Field(description="上一家公司")
#
#
# # 创建解析器
# resume_parser = PydanticOutputParser(pydantic_object=Resume)
# fixing_parser = OutputFixingParser.from_llm(parser=resume_parser, llm=chat)
#
# # 创建提示模板
# prompt_template = ChatPromptTemplate.from_messages([
#     ("system", "你是一个简历信息提取助手。请从文本中提取人员信息。\n{format_instructions}"),
#     ("human", "请从以下文本中提取人员信息：\n{text}")
# ])
#
#
# def extract_resume_info(text):
#     """提取简历信息的完整流程"""
#     try:
#         # 构建提示
#         prompt = prompt_template.format_prompt(
#             text=text,
#             format_instructions=resume_parser.get_format_instructions()
#         )
#
#         # 调用模型
#         response = chat.invoke(prompt.to_messages())
#
#         print(f"模型原始输出: {response.content}")
#
#         # 使用修复解析器处理输出
#         result = fixing_parser.parse(response.content)
#         return result
#
#     except Exception as e:
#         print(f"处理过程中出错: {e}")
#         return None
#
#
# # 测试用例
# test_texts = [
#     "张三，邮箱zhangsan@email.com，有5年工作经验，擅长Python和Java，之前在ABC公司工作",
#     "李四的联系方式是lisi@example.com，工作经验3年，技能包括JavaScript、React，上一家公司是XYZ科技",
#     "王五，工作8年，会项目管理和团队协作，邮箱wangwu@test.com，之前在DEF集团"
# ]
#
# print("简历信息提取测试:\n")
#
# for i, text in enumerate(test_texts, 1):
#     print(f"测试 {i}: {text}")
#     result = extract_resume_info(text)
#
#     if result:
#         print("✅ 提取成功:")
#         print(f"   姓名: {result.name}")
#         print(f"   邮箱: {result.email}")
#         print(f"   工作年限: {result.experience_years}")
#         print(f"   技能: {', '.join(result.skills)}")
#         print(f"   上一家公司: {result.last_company}")
#     else:
#         print("❌ 提取失败")
#
#     print("-" * 60)
    
    
import os
from typing import Dict, Any
from dotenv import load_dotenv

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

# 加载环境变量
load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("API_KEY")  # DeepSeek API Key
os.environ["OPENAI_BASE_URL"] = os.getenv("BASE_URL")  # DeepSeek Base URL


class KeyInfoExtractor:
    def __init__(self, model_name: str = "deepseek-chat", temperature: float = 0.7):
        """
    初始化提取器，创建 ChatOpenAI 实例（复用）。
    :param model_name: 模型名称
    :param temperature: 温度参数
    """
        self.llm = ChatOpenAI(model_name=model_name, temperature=temperature)

    def extract(self, query: str, format_fields: Dict[str, str]) -> Dict[str, Any]:
        """
    从用户输入中提取关键信息，并以 JSON 格式返回。
    :param query: 用户输入的文本
    :param format_fields: 需要提取的字段映射，如 {"name": "姓名", "age": "年龄"}
    :return: 解析后的字典
    """
        # 1. 将格式字段字典转为 JSON 字符串（示例：{"name": "姓名", "work_years": "工作年限"}）
        fields_json_str = str(format_fields).replace("'", '"')  # 简单转成合法JSON字符串

        # 2. 构建提示模板（明确告诉LLM需要输出哪些字段）
        template = """
        你是一个信息提取助手。请根据用户输入，提取以下字段对应的信息，并以 JSON 格式返回。

        需要提取的字段及其中文说明：
        {format_fields}

        用户输入：
        {query}

        要求：
        - 只返回 JSON 格式的数据，不要包含任何额外说明。
        - JSON 的 key 必须使用英文（与上方字段名完全一致），value 使用提取到的信息。
        - 如果某个字段在用户输入中未提及，则 value 设为 null。
        """
        prompt = PromptTemplate(template=template, input_variables=["format_fields", "query"])

        # 3. 使用 JsonOutputParser 确保输出为合法 JSON
        parser = JsonOutputParser()
        chain = prompt | self.llm | parser

        # 4. 调用链并返回结果
        try:
            result = chain.invoke({"format_fields": fields_json_str, "query": query})
            return result
        except Exception as e:
            print(f"解析或调用失败: {e}")
            return {}  # 或抛出自定义异常


if __name__ == '__main__':
    extractor = KeyInfoExtractor()

    # 示例1：个人信息
    query1 = "本人刘天王，生于浙江，2021年毕业天津大学，23年进入google公司，掌握ai,大数据开发，邮箱：123@sina.com,电话：12345，擅长表演."
    fields1 = {
        "name": "姓名",
        "work_years": "工作年限",
        "skill": "技能",
        "telephone": "联系方式",
        "email": "电邮"
    }
    result1 = extractor.extract(query1, fields1)
    print("提取结果1：", result1)

    # 示例2：书籍信息
    query2 = "《朝花夕拾》原名《旧事重提》，是现代文学家鲁迅的散文集，收录鲁迅于1926年创作的10篇回忆性散文。"
    fields2 = {
        "book_name": "书名",
        "author_name": "作者"
    }
    result2 = extractor.extract(query2, fields2)
    print("提取结果2：", result2)

