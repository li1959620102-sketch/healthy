# import os
# import threading
# from concurrent.futures import ThreadPoolExecutor, as_completed
# from typing import List, Dict, Any, Callable
# from langchain.document_loaders import PyPDFLoader, UnstructuredWordDocumentLoader
# from langchain.text_splitter import RecursiveCharacterTextSplitter, CharacterTextSplitter
# from langchain_community.document_loaders import (
#     Docx2txtLoader
# )
# from app.test.doc2txt_loader import Doc2txtLoader
# from langchain.schema import Document
# import time
# from pathlib import Path
#
#
# class DocumentProcessor:
#     """文档处理器，支持多线程和多种处理策略"""
#
#     def __init__(self, max_workers: int = 4):
#         self.max_workers = max_workers
#         self.lock = threading.Lock()
#
#     def get_file_strategy(self, file_path: str) -> Dict[str, Any]:
#         """根据文件类型返回处理策略"""
#         file_ext = Path(file_path).suffix.lower()
#
#         strategies = {
#             '.pdf': {
#                 'loader': PyPDFLoader,
#                 'splitter': RecursiveCharacterTextSplitter(
#                     chunk_size=1000,
#                     chunk_overlap=200,
#                     length_function=len,
#                 ),
#                 'description': 'PDF文档策略 - 递归字符分割'
#             },
#             '.doc': {
#                 'loader': Doc2txtLoader,
#                 'splitter': CharacterTextSplitter(
#                     chunk_size=800,
#                     chunk_overlap=100,
#                     separator="\n",
#                 ),
#                 'description': 'DOC文档策略 - 字符分割'
#             },
#             '.docx': {
#                 'loader': Docx2txtLoader,
#                 'splitter': RecursiveCharacterTextSplitter(
#                     chunk_size=1200,
#                     chunk_overlap=150,
#                     length_function=len,
#                 ),
#                 'description': 'DOCX文档策略 - 递归字符分割'
#             }
#         }
#
#         return strategies.get(file_ext, None)
#
#     def process_single_document(self, file_path: str) -> List[Document]:
#         """处理单个文档"""
#         try:
#             strategy = self.get_file_strategy(file_path)
#             if not strategy:
#                 print(f"不支持的文件类型: {file_path}")
#                 return []
#
#             print(f"处理文件: {file_path} - 策略: {strategy['description']}")
#
#             # 加载文档
#             loader = strategy['loader'](file_path)
#             documents = loader.load()
#
#             # 文本分割
#             split_documents = strategy['splitter'].split_documents(documents)
#
#             # 添加元数据
#             for doc in split_documents:
#                 doc.metadata.update({
#                     'file_path': file_path,
#                     'file_type': Path(file_path).suffix,
#                     'processed_time': time.time(),
#                     'chunk_count': len(split_documents)
#                 })
#
#             with self.lock:
#                 print(f"✓ 完成处理: {file_path} -> 生成 {len(split_documents)} 个块")
#
#             return split_documents
#
#         except Exception as e:
#             with self.lock:
#                 print(f"使用主加载器失败 {file_path}: {str(e)}，尝试备用加载器")
#                 # 使用备用加载器
#             return []
#
#     def process_documents_parallel(self, file_paths: List[str]) -> Dict[str, List[Document]]:
#         """多线程并行处理文档"""
#         results = {}
#
#         with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
#             # 提交所有任务
#             future_to_file = {
#                 executor.submit(self.process_single_document, file_path): file_path
#                 for file_path in file_paths
#             }
#
#             # 收集结果
#             for future in as_completed(future_to_file):
#                 file_path = future_to_file[future]
#                 try:
#                     documents = future.result()
#                     results[file_path] = documents
#                 except Exception as e:
#                     print(f"任务执行错误 {file_path}: {str(e)}")
#                     results[file_path] = []
#
#         return results
#
#
# class AdvancedDocumentProcessor(DocumentProcessor):
#     """高级文档处理器，支持自定义策略和进度跟踪"""
#
#     def __init__(self, max_workers: int = 4):
#         super().__init__(max_workers)
#         self.processed_count = 0
#         self.total_count = 0
#
#     def create_custom_splitter(self, file_type: str) -> Any:
#         """创建自定义分割策略"""
#         if file_type == '.pdf':
#             return RecursiveCharacterTextSplitter(
#                 chunk_size=1500,
#                 chunk_overlap=250,
#                 separators=["\n\n", "\n", "。", "！", "？", "．", "!"],
#             )
#         elif file_type in ['.doc', '.docx']:
#             return CharacterTextSplitter(
#                 chunk_size=1000,
#                 chunk_overlap=150,
#                 separator="\n",
#             )
#         else:
#             return RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
#
#     def process_with_callback(self, file_paths: List[str],
#                               callback: Callable = None) -> Dict[str, List[Document]]:
#         """带回调函数的文档处理"""
#         self.total_count = len(file_paths)
#         self.processed_count = 0
#
#         def wrapper_process(file_path: str) -> List[Document]:
#             result = self.process_single_document(file_path)
#             self.processed_count += 1
#
#             if callback:
#                 progress = (self.processed_count / self.total_count) * 100
#                 callback(file_path, progress, len(result))
#
#             return result
#
#         with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
#             future_to_file = {
#                 executor.submit(wrapper_process, file_path): file_path
#                 for file_path in file_paths
#             }
#
#             results = {}
#             for future in as_completed(future_to_file):
#                 file_path = future_to_file[future]
#                 results[file_path] = future.result()
#
#         return results
#
#
# def print_progress(file_path: str, progress: float, chunk_count: int):
#     """进度回调函数"""
#     print(f"进度: {progress:.1f}% - {file_path} -> {chunk_count} chunks")
#
#
# def check_dependencies():
#     """检查必要的依赖"""
#     dependencies = {
#         'pypdf': 'PyPDFLoader',
#         'docx2txt': 'Docx2txtLoader',
#         'unstructured': 'UnstructuredWordDocumentLoader'
#     }
#
#     missing_deps = []
#     for dep, loader_name in dependencies.items():
#         try:
#             __import__(dep)
#             print(f"✓ {dep} 已安装 - 支持 {loader_name}")
#         except ImportError:
#             print(f"✗ {dep} 未安装 - 无法使用 {loader_name}")
#             missing_deps.append(dep)
#
#     return missing_deps
#
#
# def install_missing_dependencies(dependencies):
#     """提示安装缺失的依赖"""
#     if dependencies:
#         print(f"\n请安装缺失的依赖:")
#         for dep in dependencies:
#             print(f"pip install {dep}")
#         print("\n或者安装所有推荐依赖:")
#         print("pip install pypdf docx2txt unstructured python-docx")
#     else:
#         print("✓ 所有依赖都已安装")
# def main():
#     """主函数示例"""
#     print("=== 依赖检查 ===")
#     missing_deps = check_dependencies()
#     install_missing_dependencies(missing_deps)
#     # 模拟文件路径（请替换为实际文件路径）
#     sample_files = [
#         "./docs/中华人民共和国劳动合同法.pdf",
#         "./docs/合同违法行为监督处理办法.docx",
#         "./docs/合同法案例.doc"
#     ]
#
#     # 创建实际测试文件（仅用于演示）
#     def create_sample_files():
#         for file in sample_files:
#             if not os.path.exists(file):
#                 Path(file).touch()  # 创建空文件用于演示
#
#     create_sample_files()
#
#     print("=== 基础文档处理 ===")
#     processor = DocumentProcessor(max_workers=3)
#
#     start_time = time.time()
#     results = processor.process_documents_parallel(sample_files)
#     end_time = time.time()
#
#     # 统计结果
#     total_chunks = 0
#     for file_path, documents in results.items():
#         print(f"{file_path}: {len(documents)} 个文本块")
#         total_chunks += len(documents)
#
#     print(f"\n总计处理 {len(sample_files)} 个文件，生成 {total_chunks} 个文本块")
#     print(f"处理时间: {end_time - start_time:.2f} 秒")
#
#     print("\n=== 高级文档处理（带进度回调）===")
#     advanced_processor = AdvancedDocumentProcessor(max_workers=2)
#
#     start_time = time.time()
#     advanced_results = advanced_processor.process_with_callback(
#         sample_files[:3],  # 只处理前3个文件作为示例
#         callback=print_progress
#     )
#     end_time = time.time()
#
#     print(f"高级处理时间: {end_time - start_time:.2f} 秒")
#
#     # 展示处理后的文档内容示例
#     print("\n=== 处理结果示例 ===")
#     for file_path, documents in results.items():
#         if documents:  # 只显示有结果的文档
#             print(f"\n文件: {file_path}")
#             print(f"第一个文本块预览: {documents[0].page_content[:100]}...")
#             print(f"元数据: {documents[0].metadata}")
#
#
# if __name__ == "__main__":
#     main()