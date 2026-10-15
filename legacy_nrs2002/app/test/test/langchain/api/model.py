# from openai import OpenAI
# from dotenv import load_dotenv
# import os
# load_dotenv()
# key = os.getenv('DASHSCOPE_API_KEY')  # 阿里云的（如通义千问）


from openai import OpenAI
from dotenv import load_dotenv
import os
load_dotenv()
api_key = os.getenv('API_KEY')
base_url = os.getenv('BASE_URL')

def run_model():
	# 初始化客户端(实例化OpenAI类)，将 base_url 指向 DeepSeek 的 API 端点
	client = OpenAI(
		api_key=api_key,# 请替换为你在 DeepSeek 平台获取的密钥
		base_url=base_url # DeepSeek 的 API 端点[citation:1]
	)

	# 创建聊天补全
	res = client.chat.completions.create(
		model='deepseek-chat', # 指定使用的 DeepSeek 模型[citation:6]
		messages=[
			{"role": "system", "content": "你是乐于助人的小助手，请根据用户的问题给出回答"},
			{"role": "user", "content": "你是谁?"},
		]
	)

	print(res.choices[0].message.content)
	print(res.model_dump_json())

if __name__ == '__main__':
	run_model()


# # 通过Langchain调用DeepSeek对话模型
# from langchain_openai import ChatOpenAI
# from dotenv import load_dotenv
# import os
#
# # 加载环境变量（确保.env文件中配置了DeepSeek的密钥和地址）
# load_dotenv()
#
# # 获取DeepSeek的API密钥和基础URL
# deepseek_api_key = os.getenv("API_KEY") # 从.env文件读取，也可直接替换为你的密钥
# deepseek_base_url = os.getenv("BASE_URL") # DeepSeek兼容OpenAI的API地址
#
# # 1. 获取DeepSeek对话模型实例
# chat = ChatOpenAI(
# 	model="deepseek-chat", # DeepSeek对话模型名称（固定为deepseek-chat）
# 	api_key=deepseek_api_key, # DeepSeek的API密钥
# 	base_url=deepseek_base_url or "https://api.deepseek.com/v1", # DeepSeek兼容OpenAI的API基础地址
# 	temperature=0.7 # 可根据需要调整随机性（0-1之间）
# )
#
# # 2. 调用模型（对话格式与OpenAI兼容，支持system/user/assistant角色）
# res = chat.invoke([
# 	{"role": "system", "content": "你是乐于助人的小助手，请根据用户问题进行回答"},
# 	{"role": "user", "content": "你是谁?"},
# ])
#
# # 3. 处理返回结果（两种常用方式）
# print("原始JSON结果：")
# print(res.model_dump_json(indent=2)) # 格式化输出完整结果
#
# print("\n模型回答内容：")
# print(res.content) # 直接提取回答文本