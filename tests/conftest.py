# -*- coding: utf-8 -*-
"""pytest 全局配置。

必须在任何 app 模块被导入前设置 NO_PROXY：本机开着代理时，访问 127.0.0.1
会被拦成 502，导致 MySQL / Redis / Milvus 的连接被误判为不可用。
"""
import os

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
