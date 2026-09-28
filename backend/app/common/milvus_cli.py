from pymilvus import MilvusClient
import os

class MilvusManager:
    def __init__(self):
        self.client = MilvusClient(uri="http://192.168.26.129:19530")

    def insert_data(self, collection_name: str, data: list):
        """统一的插入接口，data 是包含 id, text, vector, metadata 的字典列表"""
        try:
            res = self.client.insert(collection_name=collection_name, data=data)
            return res
        except Exception as e:
            print(f"❌ 插入失败：{e}")
            return None