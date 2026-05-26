from pymongo import MongoClient

# 连接本地默认端口的 MongoDB
client = MongoClient('mongodb://localhost:27017/')

# 测试连接是否成功（可选）
try:
    client.admin.command('ping')
    print("成功连接到 MongoDB")
except Exception as e:
    print("连接失败:", e)


