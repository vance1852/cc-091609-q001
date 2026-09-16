# 中药材来源鉴别证据

该项目保存送检批次、封签分样、检验方法和签发意见的领域资料。原始观察与方法版本是鉴定证据的一部分，后续复检不得覆盖既有记录。

`domain/contracts.py` 定义样品、检验和意见结构，`fixtures/specimen_chain.json` 是一次脱敏的复检与封签争议记录。项目使用 Python 3.11，可执行 `python -m compileall domain` 检查契约语法。
