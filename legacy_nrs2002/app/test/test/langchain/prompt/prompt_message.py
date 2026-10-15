# 存储对话历史内容
from langchain_core.prompts import ChatPromptTemplate,MessagesPlaceholder
from langchain_core.messages import HumanMessage,AIMessage
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os
# 加载环境变量
load_dotenv()
 
# 配置 DeepSeek 模型
chat = ChatOpenAI(
    model='deepseek-chat',
    openai_api_key=os.getenv("API_KEY"),
    openai_api_base=os.getenv("BASE_URL"),
    streaming=True,
    temperature=0.7
)
 
# 存储对话历史内容
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个人工智能助手"),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{query}")
])
 
messages = prompt.format_messages(
    history=[HumanMessage(content="(1+2*3)*5/7等于几?,输出的格式为json，如：{requestion:s,calc:'x+y',result:n}"),
             # AIMessage(content="1+2*3=7")
             ],
    query="输出最终答案"
)
response = chat.invoke(messages)
print(response.content)