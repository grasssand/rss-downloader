## v0.2.0 (2025-09-13)

### Fix

- **parser**: 匹配规则忽略大小写

### Refactor

- 异步重构
- **downloader**: 优化下载器初始化时异常处理
- **database**: 设置 sqlite 连接参数 `detect_types`
- **models**: 优化 DefaultEntry 解析
- **web**: 重构优化 web
- **main**: 优化异常处理
- **services**: 集中核心服务实例容器
- **database**: 解耦数据库配置
- **config**: 优化配置更新、重载

## v0.1.3 (2025-09-09)

### Refactor

- **models**: 优化 Entry model
- 删除无用类和方法

## v0.1.2 (2025-09-08)

### Fix

- **models**: Entry model 设置默认值
- **database**: 修复 published_time 查询

## v0.1.1 (2025-09-08)

### Feat

- 添加用于配置和查看记录的 FastAPI Web 界面
- RSS 解析和下载
- 数据库和下载器客户端的基础功能
- Initial commit

### Refactor

- **main**: 优化结果输出
- **web**: 重构 test-downloader API (测试下载器连接)
- **models**: 重构数据模型
