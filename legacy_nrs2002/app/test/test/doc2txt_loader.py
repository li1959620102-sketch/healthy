# import os
# import logging
# import tempfile
# from abc import ABC
# from typing import Any, List, Union
# from urllib.parse import urlparse
#
# import requests
# from langchain_core.documents import Document
# from langchain_community.document_loaders.base import BaseLoader
# from langchain_community.document_loaders import (
#     Docx2txtLoader
# )
# logger = logging.getLogger(__name__)
#
#
# class Doc2txtLoader(BaseLoader, ABC):
#     def __init__(self, file_path: str):
#         """Initialize with file path."""
#         self.file_path = str(file_path)
#         self.original_file_path = self.file_path
#         if "~" in self.file_path:
#             self.file_path = os.path.expanduser(self.file_path)
#
#         # If the file is a web path, download it to a temporary file, and use that
#         if not os.path.isfile(self.file_path) and self._is_valid_url(self.file_path):
#             r = requests.get(self.file_path)
#
#             if r.status_code != 200:
#                 raise ValueError(
#                     "Check the url of your file; returned status code %s"
#                     % r.status_code
#                 )
#
#             self.web_path = self.file_path
#             self.temp_file = tempfile.NamedTemporaryFile()
#             self.temp_file.write(r.content)
#             self.file_path = self.temp_file.name
#         elif not os.path.isfile(self.file_path):
#             raise ValueError("File path %s is not a valid file or url" % self.file_path)
#
#     def load(self) -> List[Document]:
#         file_path = self.file_path  # 定义在try外部，确保except中可以访问
#         temp_docx_path = None
#
#         try:
#             # 检查文件是否存在
#             if not os.path.exists(file_path):
#                 raise FileNotFoundError(f"文件不存在: {file_path}")
#
#             # 检查文件大小
#             file_size = os.path.getsize(file_path)
#             if file_size == 0:
#                 raise ValueError("文件为空")
#
#             # 显式导入client
#             from comtypes import client
#             word = client.CreateObject('Word.Application')
#             word.Visible = False
#
#             abs_file_path = os.path.abspath(file_path)
#             doc = word.Documents.Open(abs_file_path)
#
#             # 创建临时文件
#             temp_docx_path = tempfile.NamedTemporaryFile(suffix='.docx', delete=False).name
#             doc.SaveAs(temp_docx_path, FileFormat=16)  # 16表示docx格式
#             doc.Close()
#             word.Quit()
#
#             # 使用Docx2txtLoader加载转换后的文件
#             loader = Docx2txtLoader(temp_docx_path)
#             docs = loader.load()
#
#             return docs
#
#         except ImportError:
#             logger.error("comtypes未安装，无法处理.doc文件")
#             raise
#         except Exception as e:
#             logger.error(f".doc文件处理失败：{file_path}，错误：{str(e)}")
#             # 返回包含错误信息的文档，而不是完全失败
#             return [Document(
#                 page_content=f"无法处理DOC文件: {str(e)}",
#                 metadata={"source": file_path, "error": True, "error_message": str(e)}
#             )]
#         finally:
#             # 清理临时文件
#             if temp_docx_path and os.path.exists(temp_docx_path):
#                 try:
#                     os.remove(temp_docx_path)
#                 except:
#                     pass
#
#     @staticmethod
#     def _is_valid_url(url: str) -> bool:
#         """Check if the url is valid."""
#         parsed = urlparse(url)
#         return bool(parsed.netloc) and bool(parsed.scheme)