# Claude Code 入口

本仓库所有协作规则以 [AGENTS.md](AGENTS.md) 为准，文档职责与真源顺序见 [docs/README.md](docs/README.md)。

特别注意：`ark-quant` 是落实投资体系的工具；代码、配置和 Web 页面无权反向定义战略或尚未定型的战术。修改前先确认对应规则文档的状态。

常用命令：

```bash
source .venv/bin/activate
python3 -m unittest discover -s tests -v
python3 run_app.py
python3 -m strategies.cb_rotation.run
python3 -m strategies.stock_smallcap.run
python3 rebalance.py
```

具体操作和工程现状分别见：

- [日常运行手册](docs/使用/日常运行手册.md)
- [系统架构](docs/工程/系统架构.md)
- [当前实现状态](docs/工程/当前实现状态.md)
