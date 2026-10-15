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
# template = """
# 你是一个简历信息提取助手。请从用户输入中提取关键信息，并以JSON格式返回。
#
# JSON格式要求：
# {{
#     "nane": 姓名,\n
#     "age": 年龄,\n
#     "location": 位置,\n
#     "school": 毕业学校,\n
#     "major": 专业,\n
#     "work_time": 工作年限,\n
#     "skills": 技能,\n
#     "work_experience": 经验,\n
#     "contact": 联系方式,\n
#     "other": 其他
# }}
# 示例：工作年限是当前系统时间减去毕业时间，比如他2022年毕业，2022年工作，当前系统时间时2026年，那么工作年限就是2026-2022=4年
#
# 用户输入:
# {query}
#
# 请只返回JSON格式的日期信息，不要包含其他内容。
# """
# prompt = PromptTemplate(
#     template=template
# )
# query = ("我叫xxy，今年27岁，籍贯是HN省，电话号码是13812345678，长沙理工大学城南学院2020年毕业，本科学历，学士学位，专业是计算机科学，"
#          "有两段工作经验，一份在HW公司，当软件测试，干了2年，一份是在xiaomi，干的是嵌入式工程师，干了三年，"
#          "熟练使用python、java，有英语六级整数，爱好是打羽毛球")
#
# input_format = prompt.format(query=query)
# output = chat.invoke(input_format)
# print("Json格式的输出：", output.content)




from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from dotenv import load_dotenv
import os
import json

# 加载环境变量
load_dotenv()

os.environ["OPENAI_API_KEY"] = os.getenv("API_KEY")  # DeepSeek的API密钥
os.environ["OPENAI_BASE_URL"] = os.getenv("BASE_URL")  # DeepSeek基础URL

# 实例化DeepSeek模型
chat = ChatOpenAI(
    model_name="deepseek-chat",
    temperature=0.7
)


# ============================================================
# 1. 定义 Pydantic 数据模型（替代 JSON 格式说明）
# ============================================================
class ResumeInfo(BaseModel):
    """简历信息模型"""
    name: str = Field(description="姓名")
    age: int = Field(description="年龄")
    location: str = Field(description="籍贯/位置")
    school: str = Field(description="毕业学校")
    major: str = Field(description="专业")
    work_time: int = Field(description="工作年限（当前年份减去毕业年份）")
    skills: str = Field(description="掌握的技能，用逗号分隔")
    work_experience: str = Field(description="工作经验描述，包含公司和岗位")
    contact: str = Field(description="联系方式（电话号码等）")
    other: str = Field(description="其他补充信息，如爱好、证书等")


# ============================================================
# 2. 创建 PydanticOutputParser 实例
# ============================================================
parser = PydanticOutputParser(pydantic_object=ResumeInfo)

# ============================================================
# 3. 定义提示词模板（使用 parser 自动生成格式指令）
# ============================================================
template = """
你是一个简历信息提取助手。请从用户输入中提取关键信息，并以JSON格式返回。

{format_instructions}

注意：工作年限的计算方式：当前年份（{current_year}）减去毕业年份。
例如：2022年毕业 → 工作年限 = {current_year} - 2022

用户输入:
{query}

请只返回JSON格式的信息，不要包含其他内容。
"""

# 获取当前年份
import datetime

current_year = datetime.datetime.now().year

prompt = PromptTemplate(
    template=template,
    # 通过 partial_variables 自动注入格式指令和当前年份
    partial_variables={
        "format_instructions": parser.get_format_instructions(),
        "current_year": str(current_year)
    }
)

# ============================================================
# 4. 用户输入和调用
# ============================================================
query = (
    "我叫xxy，今年27岁，籍贯是HN省，电话号码是13812345678，长沙理工大学城南学院2020年毕业，本科学历，学士学位，专业是计算机科学，"
    "有两段工作经验，一份在HW公司，当软件测试，干了2年，一份是在xiaomi，干的是嵌入式工程师，干了三年，"
    "熟练使用python、java，有英语六级证书，爱好是打羽毛球"
)

# 生成最终提示词
input_prompt = prompt.format(query=query)

# 调用模型
output = chat.invoke(input_prompt)

print("=" * 50)
print("原始模型输出：")
print("=" * 50)
print(output.content)

# ============================================================
# 5. 使用 PydanticOutputParser 解析为结构化对象
# ============================================================
try:
    # 解析输出，得到 ResumeInfo 对象
    parsed_result = parser.invoke(output)

    print("\n" + "=" * 50)
    print("Pydantic 结构化输出：")
    print("=" * 50)
    print(parsed_result)

    # 可以像普通对象一样访问属性
    print("\n" + "=" * 50)
    print("按字段访问：")
    print("=" * 50)
    print(f"姓名: {parsed_result.name}")
    print(f"年龄: {parsed_result.age}")
    print(f"学校: {parsed_result.school}")
    print(f"专业: {parsed_result.major}")
    print(f"工作年限: {parsed_result.work_time}")
    print(f"技能: {parsed_result.skills}")
    print(f"工作经验: {parsed_result.work_experience}")

    # 也可以转为字典
    print("\n" + "=" * 50)
    print("转为字典：")
    print("=" * 50)
    print(parsed_result.dict())

    # 转为 JSON 字符串
    print("\n" + "=" * 50)
    print("转为 JSON 字符串：")
    print("=" * 50)
    print(parsed_result.json(indent=2))

except Exception as e:
    print(f"\n解析失败：{e}")
    print("尝试手动解析原始输出...")
    # 如果 Pydantic 解析失败，可以尝试用 json.loads 手动解析
    try:
        raw_json = json.loads(output.content)
        print("手动解析成功：", raw_json)
    except json.JSONDecodeError as je:
        print(f"JSON解析也失败：{je}")