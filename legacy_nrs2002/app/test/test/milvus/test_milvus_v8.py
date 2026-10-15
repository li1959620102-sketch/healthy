# 文件名: test_milvus_v8_fixed.py
from pymilvus import MilvusClient, DataType
import numpy as np
import random
import logging

# 配置日志
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ================== 配置信息 ==================
milvus_uri = "http://localhost:19530"
db_name = "test_database"
collection_name = "test_collection"
collection_alias = "test_collection_alias"
dim = 128  # 向量维度


def safe_drop_collection(client, collection_name, known_aliases=None):
	"""
    安全删除集合，先删除所有关联别名

    Args:
        client: MilvusClient 实例
        collection_name: 集合名称
        known_aliases: 已知的别名列表（字符串或列表）
    """
	# 如果提供了已知别名，先尝试删除
	if known_aliases:
		if isinstance(known_aliases, str):
			known_aliases = [known_aliases]

		for alias in known_aliases:
			try:
				# 使用正确的方式删除别名
				client.drop_alias(
					collection_name=collection_name,
					alias=alias,
					timeout=10  # 明确指定超时时间（数字）
				)
				logger.info(f"已删除别名: {alias}")
			except Exception as e:
				logger.warning(f"删除别名 {alias} 时出错: {str(e)}")

	# 删除集合
	try:
		client.drop_collection(
			collection_name=collection_name,
			timeout=10  # 明确指定超时时间（数字）
		)
		logger.info(f"已删除集合: {collection_name}")
	except Exception as e:
		logger.error(f"删除集合失败: {str(e)}")
		raise


def main():
	# 1. 初始化 MilvusClient
	client = MilvusClient(uri=milvus_uri, db_name=db_name)
	logger.info(f"成功连接到 Milvus: {milvus_uri}, 数据库: {db_name}")

	try:
		# 2. 准备集合：如果已存在则安全删除
		if client.has_collection(collection_name):
			# 传入已知的别名
			safe_drop_collection(client, collection_name, collection_alias)

		# 3. 定义集合并创建
		schema = client.create_schema(
			auto_id=True,
			description="测试集合"
		)
		schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
		schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
		schema.add_field(field_name="category", datatype=DataType.INT64)
		schema.add_field(field_name="name", datatype=DataType.VARCHAR, max_length=100)

		# 使用 prepare_index_params 代替字典
		index_params = client.prepare_index_params()
		index_params.add_index(
			field_name="vector",
			index_type="IVF_FLAT",
			metric_type="L2",
			params={"nlist": 100}
		)

		# 创建集合时传入 IndexParams 对象
		client.create_collection(
			collection_name=collection_name,
			schema=schema,
			index_params=index_params,
			timeout=10  # 明确指定超时时间
		)
		logger.info(f"已创建集合: {collection_name}")

		# 4. 插入数据
		num_entities = 30
		vectors = [
			np.random.rand(dim).astype(np.float32).tolist()
			for _ in range(num_entities)
		]
		categories = [random.randint(1, 5) for _ in range(num_entities)]
		names = [f"item_{i}" for i in range(num_entities)]

		# 直接传字典列表
		data_to_insert = [
			{"vector": vec, "category": cat, "name": name}
			for vec, cat, name in zip(vectors, categories, names)
		]
		insert_result = client.insert(collection_name, data_to_insert)
		logger.info(f"已插入 {insert_result['insert_count']} 条数据")

		# 5. 加载集合到内存
		client.load_collection(collection_name)
		logger.info("已加载集合到内存")

		# 6. 创建集合别名
		client.create_alias(
			collection_name=collection_name,
			alias=collection_alias,
			timeout=10
		)
		logger.info(f"已为集合创建别名: {collection_alias}")

		# 7. 向量搜索
		search_vector = np.random.rand(dim).astype(np.float32).tolist()
		search_params = {"metric_type": "L2", "params": {"nprobe": 10}}

		logger.info("=== 向量搜索结果 ===")
		search_results = client.search(
			collection_name=collection_name,
			data=[search_vector],
			anns_field="vector",
			search_params=search_params,
			limit=3,
			output_fields=["category", "name"]
		)

		for hits in search_results:
			for idx, hit in enumerate(hits, 1):
				logger.info(
					f"排名 {idx}: ID={hit['id']}, "
					f"距离={hit['distance']:.4f}, "
					f"类别={hit['category']}, "
					f"名称={hit['name']}"
				)

		# 8. 标量查询
		logger.info("=== 标量查询结果 (category=3) ===")
		query_results = client.query(
			collection_name=collection_name,
			filter="category == 3",
			output_fields=["id", "category", "name"],
			limit=5
		)
		for item in query_results:
			logger.info(
				f"ID={item['id']}, "
				f"类别={item['category']}, "
				f"名称={item['name']}"
			)

		# 9. 修改数据
		logger.info("=== 修改数据 ===")
		items_to_update = client.query(
			collection_name=collection_name,
			filter="category == 2",
			output_fields=["id", "vector", "name"]
		)
		if items_to_update:
			ids_to_delete = [item["id"] for item in items_to_update]
			delete_result = client.delete(collection_name, ids=ids_to_delete)
			logger.info(f"已删除 {delete_result['delete_count']} 条数据用于更新")

			new_data = []
			for item in items_to_update:
				new_data.append({
					"vector": item["vector"],
					"category": 99,
					"name": item["name"]
				})
			update_result = client.insert(collection_name, new_data)
			logger.info(f"已插入 {update_result['insert_count']} 条更新后的数据")

		# 10. 删除数据
		logger.info("=== 删除数据 ===")
		delete_result = client.delete(collection_name, filter="category == 1")
		logger.info(f"已删除 {delete_result['delete_count']} 条满足条件的数据")

		# 11. 清理工作
		# 先删别名 → 再释放集合 → 最后删集合/库
		try:
			client.drop_alias(
				collection_name=collection_name,
				alias=collection_alias,
				timeout=10
			)
			logger.info(f"已删除集合别名: {collection_alias}")
		except Exception as e:
			logger.warning(f"删除别名时出错: {str(e)}")

		client.release_collection(collection_name)
		logger.info("已释放集合内存")

		client.drop_collection(
			collection_name=collection_name,
			timeout=10
		)
		logger.info(f"已删除集合: {collection_name}")

		# 删除数据库
		# 先切换回 default 数据库
		client.close()
		default_client = MilvusClient(uri=milvus_uri, db_name="default")
		default_client.drop_database(db_name)
		default_client.close()
		logger.info(f"已删除数据库: {db_name}")

	except Exception as e:
		logger.error(f"操作出错: {str(e)}")
		raise
	finally:
		try:
			client.close()
			logger.info("已断开与 Milvus 的连接")
		except:
			pass


if __name__ == "__main__":
	main()