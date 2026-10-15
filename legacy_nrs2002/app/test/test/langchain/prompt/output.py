# from langchain_core.messages import SystemMessage, HumanMessage
# from langchain_core.output_parsers import JsonOutputParser
# from langchain_core.prompts import ChatPromptTemplate
# from langchain_openai import ChatOpenAI
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
# chat_prompt_template = ChatPromptTemplate.from_messages([
#     ("system","你是一个靠谱的{role}"),
#     ("human","{question}")
# ])
# parser = JsonOutputParser()
#
# # 方式1：
# # result = chat.invoke(chat_prompt_template.format_messages(role="人工智能专家",question="人工智能用英文怎么说？问题用q表示，答案用a表示，返回一个JSON格式"))
# # print(result.content)
# # print(type(result))
#
# # 方式2： 使用LCEL语法组合一个简单的链
# chain = chat_prompt_template | chat | parser
# response = chain.invoke({"role":"人工智能专家","question" : "人工智能用英文怎么说？问题用question表示，答案用answer表示，返回一个JSON格式"})
# print(response)


# # 定义Json解析器
# from langchain_core.messages import SystemMessage, HumanMessage
# from langchain_core.output_parsers import JsonOutputParser
# from langchain_core.prompts import PromptTemplate
# # from langchain_community.chat_models import ChatOpenAI
# from langchain_openai import ChatOpenAI
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
# # 定义Json解析器
# parser = JsonOutputParser()
# # 定义提示词模版
# # 注意，提示词模板中需要部分格式化解析器的格式要求format_instructions
# prompt_template = PromptTemplate(
#     template="回答用户的查询.\n{format_instructions}\n{query}\n",
#     input_variables=["query"],
#     partial_variables={"format_instructions": parser.get_format_instructions()},
# )
# # 5.使用LCEL语法组合一个简单的链
# chain = prompt_template | chat | parser
# # 6.执行链
# output = chain.invoke({"query": "给我讲一个笑话"})
#
# print(output)

# 输出指定的Json格式
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
# from langchain_community.chat_models import ChatOpenAI
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os
# 加载环境变量
load_dotenv()

os.environ["OPENAI_API_KEY"] = os.getenv("API_KEY")  # DeepSeek的API密钥
os.environ["OPENAI_BASE_URL"] = os.getenv("BASE_URL")  # DeepSeek基础URL

# 实例化DeepSeek模型（替代原OpenAI实例）
chat = ChatOpenAI(
    model_name="deepseek-chat",  # DeepSeek对话模型固定名称
    temperature=0.7  # 可调整随机性
)
# 定义Json解析器
template = """
你是一个日期提取助手。请从用户输入中提取日期信息，并以JSON格式返回。

JSON格式要求：
{{
    "year": 年份,\n
    "month": 月份,\n
    "day": 日期
}}

用户输入:
{query}

请只返回JSON格式的日期信息，不要包含其他内容。
"""
prompt = PromptTemplate(
    template=template
)
query = "2025年十二月31日天气晴..."

input_format = prompt.format(query=query)
output = chat.invoke(input_format)
print("Json格式的输出：", output.content)