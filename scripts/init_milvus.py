from pymilvus import MilvusClient, DataType

# 注意：要用虚拟机的 IP
client = MilvusClient(uri="http://192.168.26.129:19530")

# 定义 Collection Schema
schema = client.create_schema(
    auto_id=False, enable_dynamic_field=True
)
schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535) # 爆款文案或竞品名称
schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=1024)  # 向量维度，必须全队统一！
schema.add_field(field_name="metadata", datatype=DataType.JSON)                 # 存放平台、价格、时间等

# 创建索引（为了快速检索）
index_params = client.prepare_index_params()
index_params.add_index(field_name="vector", metric_type="COSINE", index_type="IVF_FLAT", nlist=1024)

# 创建集合
client.create_collection(collection_name="knowledge_base", schema=schema, index_params=index_params)
print("✅ Milvus 集合初始化完成！")
