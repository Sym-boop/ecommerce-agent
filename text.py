from pymilvus import connections, utility
import redis

# 测试 Milvus 连接
try:
    connections.connect("default", host="192.168.26.129", port="19530")
    print("✅ Milvus 连接成功！版本：", utility.get_server_version())
except Exception as e:
    print("❌ Milvus 连接失败：", e)

# 测试 Redis 连接
try:
    r = redis.Redis(host='192.168.26.129', port=16379, decode_responses=True)
    r.ping()
    print("✅ Redis 连接成功！")
except Exception as e:
    print("❌ Redis 连接失败：", e)