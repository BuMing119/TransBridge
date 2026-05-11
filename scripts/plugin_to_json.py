
"""
插件文件转JSON脚本
从.esp/.esm插件文件中提取可翻译字符串并转换为JSON格式
"""
import json
import logging
from pathlib import Path
import sys

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.transbridge.parser.plugin_parser import PluginParser


def setup_logging():
    """设置日志记录"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )
    return logging.getLogger("PluginToJson")


def plugin_to_json(plugin_path: Path, output_path: Path, skip_empty: bool = True):
    """
    将插件文件转换为JSON格式

    Args:
        plugin_path: 插件文件路径(.esp/.esm)
        output_path: 输出JSON文件路径
        skip_empty: 是否跳过空字符串(默认为True)
    """
    logger = setup_logging()

    # 验证输入文件存在
    if not plugin_path.exists():
        logger.error(f"插件文件不存在: {plugin_path}")
        return False

    # 创建输出目录(如果不存在)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"开始处理插件文件: {plugin_path}")
    logger.info(f"输出文件: {output_path}")

    # 创建解析器并解析插件
    parser = PluginParser()

    # 进度回调函数
    def progress_callback(current: int, total: int, description: str):
        if current % 100 == 0 or current == total:
            logger.info(f"进度: {current}/{total} - {description}")

    # 解析插件文件
    entries = parser.parse_plugin(
        plugin_path,
        progress_callback=progress_callback,
        skip_empty=skip_empty
    )

    if not entries:
        logger.warning("未提取到任何翻译条目")
        return False

    logger.info(f"成功提取 {len(entries)} 个翻译条目")

    # 转换为字典列表
    entries_dict = [entry.to_dict() for entry in entries]

    # 写入JSON文件
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(entries_dict, f, ensure_ascii=False, indent=2)

    logger.info(f"成功写入JSON文件: {output_path}")
    return True


def main():
    """主函数"""
    # 配置日志
    logger = setup_logging()

    # 默认输入输出路径
    default_plugin_path = Path(f"D:/MyProgram/buming1170/mods/VIGILANT SE v1801/Vigilant.esm")
    default_output_path = Path(f"D:/MyProgram/buming1170/mods/VIGILANT SE v1801/Vigilant1.json")

    # 从命令行参数获取路径
    if len(sys.argv) >= 2:
        plugin_path = Path(sys.argv[1])
    else:
        plugin_path = default_plugin_path

    if len(sys.argv) >= 3:
        output_path = Path(sys.argv[2])
    else:
        output_path = default_output_path

    # 执行转换
    success = plugin_to_json(plugin_path, output_path)

    if success:
        logger.info("转换完成!")
        sys.exit(0)
    else:
        logger.error("转换失败!")
        sys.exit(1)


if __name__ == "__main__":
    main()
