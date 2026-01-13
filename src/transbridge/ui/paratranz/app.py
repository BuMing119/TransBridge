import sys
from PyQt6.QtWidgets import QApplication
from .main_window import ParatranzMainWindow


def main():
    """启动 Paratranz UI 应用"""
    app = QApplication(sys.argv)

    # 设置应用程序属性
    app.setApplicationName("TransBridge - Paratranz 翻译管理工具")
    app.setOrganizationName("TransBridge")

    # 创建并显示主窗口
    main_window = ParatranzMainWindow()
    main_window.show()

    # 运行应用程序
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
