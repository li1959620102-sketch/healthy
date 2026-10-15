from pymilvus import MilvusClient, DataType
import numpy as np

# 核心配置：128维二进制向量（需打包为16字节，128bit=16B）
TARGET_DIM = 128
collection_name = "binary_vectors"


def main():
	# 1. 初始化客户端并清理旧环境
	client = MilvusClient(uri="tcp://localhost:19530")

	if client.has_collection(collection_name):
		client.drop_collection(collection_name)
		print(f"已强制删除旧集合 {collection_name}")

	# 2. 创建集合（明确二进制向量字段）
	schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=True)
	schema.add_field(
		field_name="id",
		datatype=DataType.INT64,
		is_primary=True
	)
	schema.add_field(
		field_name="binary_vector",
		datatype=DataType.BINARY_VECTOR,  # 二进制向量类型
		dim=TARGET_DIM
	)

	index_params = client.prepare_index_params()
	index_params.add_index(
		field_name="binary_vector",
		index_type="BIN_FLAT",
		metric_type="HAMMING"
	)

	client.create_collection(
		collection_name=collection_name,
		schema=schema,
		index_params=index_params
	)
	print(f"集合 {collection_name} 创建成功")

	# 验证集合维度
	coll_info = client.describe_collection(collection_name)
	vec_field = next(f for f in coll_info["fields"] if f["name"] == "binary_vector")
	actual_dim = vec_field["params"]["dim"]
	print(f"集合维度校验通过：{actual_dim} 维")

	# 3. 生成二进制向量（正确打包为字节流）
	num_vectors = 1000
	vectors_np = np.random.randint(0, 2, size=(num_vectors, TARGET_DIM), dtype=np.uint8)
	print(f"生成向量数组形状：{vectors_np.shape}（应显示 (1000, 128)）")

	# 二进制向量转换为字节流（128位=16字节）
	vectors_bytes = [np.packbits(vec).tobytes() for vec in vectors_np]

	# 验证字节长度
	sample_byte_len = len(vectors_bytes[0])
	if sample_byte_len != TARGET_DIM // 8:
		raise ValueError(f"二进制向量打包错误！预期 {TARGET_DIM // 8} 字节，实际 {sample_byte_len} 字节")
	print(f"二进制向量打包校验通过：{sample_byte_len} 字节（128bit）")

	# 构造插入数据
	data = [{"binary_vector": vec_bytes} for vec_bytes in vectors_bytes]

	# 4. 插入数据（修复计数打印）
	insert_result = client.insert(collection_name=collection_name, data=data)
	# insert_count是整数，直接打印即可，无需len()
	print(f"成功插入 {insert_result['insert_count']} 条数据")

	# 5. 搜索验证
	query_vector_np = np.random.randint(0, 2, size=(TARGET_DIM,), dtype=np.uint8)
	query_vector_bytes = np.packbits(query_vector_np).tobytes()
	client.flush(collection_name=collection_name)

	search_results = client.search(
		collection_name=collection_name,
		data=[query_vector_bytes],
		anns_field="binary_vector",
		search_params={"params": {}},
		limit=5,
		output_fields=["id"]
	)

	print("\n=== 汉明距离 Top5 相似结果 ===")
	for hits in search_results:
		for rank, hit in enumerate(hits, 1):
			print(f"  排名 {rank}: ID={hit['id']}, 汉明距离={hit['distance']}")


if __name__ == "__main__":
	main()