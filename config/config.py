from pyscfg import SimpleConfigs

# 创建配置管理器（默认使用 configs.yml）
cfg = SimpleConfigs()

# server
cfg["server.port"] = 8080      # int 类型
cfg["server.ip"] = ""    # bool 类型
cfg["server.username"] = ""  # list 类型
cfg["server.password"] = ""          # float 类型

# 读取配置（类型完全保留）
# port = cfg["server.port"]      # 返回 int 8080
# debug = cfg["feature.debug"]   # 返回 bool True