# 少量示例模版
# 使用新的导入路径
from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
 
# # 1、创建示例集合
# examples = [
#     {"input": "北京天气怎么样", "output": "北京市"},
#     {"input": "南京下雨吗", "output": "南京市"},
#     {"input": "武汉热吗", "output": "武汉市"}
# ]
#
# # 2、创建PromptTemplate实例
# example_prompt = PromptTemplate.from_template(
#     template="Input: {input}\nOutput: {output}"
# )
#
# # 3、创建FewShotPromptTemplate实例
# prompt = FewShotPromptTemplate(
#     examples=examples,
#     example_prompt=example_prompt,
#     suffix="Input: {input}\nOutput:",  # 要放在示例后面的提示模板字符串。
#     input_variables=["input"]  # 传入的变量
# )
#
# # 4、调用
# result = prompt.invoke({"input": "深圳有多大的面积?"})
# print("===Prompt===")
# print(result)



# #1、创建提示模板
# from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
# from langchain_openai import ChatOpenAI
# from dotenv import load_dotenv
# import os
#
# # 加载环境变量
# load_dotenv()
#
# # 初始化 DeepSeek 模型
# chat = ChatOpenAI(
#     model="deepseek-chat",
#     temperature=0.7,
#     openai_api_key=os.getenv("API_KEY"),
#     openai_api_base=os.getenv("BASE_URL")
# )
#
# # 创建提示模板，配置一个提示模板，将一个示例格式化为字符串
# prompt_template = "你是一个数学专家,算式： {input} 值： {output} 使用： {description} "
# # 这是一个提示模板，用于设置每个示例的格式
# prompt_sample = PromptTemplate.from_template(prompt_template)
# # 2、提供示例
# examples = [
#     {"input": "2+2", "output": "4", "description": "加法运算"},
#     {"input": "5-2", "output": "3", "description": "减法运算"},
# ]
#
# # 3、创建一个FewShotPromptTemplate对象
# prompt = FewShotPromptTemplate(
#     examples=examples,
#     example_prompt=prompt_sample,
#     suffix="你是一个数学专家,算式: {input} 值: {output}",
#     input_variables=["input", "output"]
# )
# print(prompt.invoke({"input": "2*5", "output": "10"}))
#
# # 4、大模型调用
# result = chat.invoke(prompt.invoke({"input": "2*5", "output": "10"}))
# print(result.content)  # 使用: 乘法运算