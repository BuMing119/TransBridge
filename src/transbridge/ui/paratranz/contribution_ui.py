from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QMessageBox, QComboBox, QSplitter, QFrame, QSpinBox,
    QDateEdit
)
from PyQt6.QtCharts import (
    QChart, QChartView, QPieSeries, QBarSeries, QBarSet, QValueAxis
)

from PyQt6.QtCore import Qt, QDate, QDateTime
from PyQt6.QtGui import QPainter
from src.transbridge.paratranz.api.paratranz_contribution_api import ParatranzContributionAPI


class ContributionFilterDialog(QDialog):
    """
    贡献统计筛选对话框
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("筛选贡献统计")
        self.resize(400, 300)

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # 表单布局
        form_layout = QFormLayout()

        # 用户ID
        self.user_id_input = QSpinBox()
        self.user_id_input.setMinimum(1)
        self.user_id_input.setMaximum(999999)
        self.user_id_input.setSpecialValueText("所有用户")
        self.user_id_input.setValue(0)
        form_layout.addRow("用户ID (0表示所有用户):", self.user_id_input)

        # 起始日期
        self.start_date_input = QDateEdit(calendarPopup=True)
        self.start_date_input.setDate(QDate.currentDate().addMonths(-1))
        form_layout.addRow("起始日期:", self.start_date_input)

        # 结束日期
        self.end_date_input = QDateEdit(calendarPopup=True)
        self.end_date_input.setDate(QDate.currentDate())
        form_layout.addRow("结束日期:", self.end_date_input)

        # 语言
        self.lang_input = QComboBox()
        self.lang_input.setEditable(True)
        self.lang_input.addItem("")
        self.lang_input.addItems([
            "zh-CN", "zh-TW", "ja", "ko", "fr", "de", "es", "ru", "pt"
        ])
        form_layout.addRow("语言:", self.lang_input)

        layout.addLayout(form_layout)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.apply_button = QPushButton("应用")
        self.apply_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(self.apply_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def get_filter_options(self):
        """获取筛选选项"""
        user_id = self.user_id_input.value()
        start_date = self.start_date_input.date().toString(Qt.DateFormat.ISODate)
        end_date = self.end_date_input.date().toString(Qt.DateFormat.ISODate)

        # 转换为毫秒时间戳
        start_timestamp = int(QDateTime.fromString(start_date, Qt.DateFormat.ISODate).toMSecsSinceEpoch())
        end_timestamp = int(QDateTime.fromString(end_date, Qt.DateFormat.ISODate).toMSecsSinceEpoch())

        return {
            "user_id": user_id if user_id > 0 else None,
            "since": start_timestamp,
            "utils": end_timestamp,
            "lang": self.lang_input.currentText().strip() or None
        }


class ContributionUI(QWidget):
    """
    Paratranz 贡献统计界面
    """

    def __init__(self, client_config, parent=None):
        super().__init__(parent)
        self.client_config = client_config
        self.api = ParatranzContributionAPI(
            token=client_config.token,
            timeout=client_config.timeout,
            config=client_config
        )

        self.contributions = []
        self.project_id = None
        self.filter_options = {}

        self.init_ui()

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()

        # 顶部工具栏
        toolbar_layout = QHBoxLayout()

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.load_contributions)

        self.filter_button = QPushButton("筛选")
        self.filter_button.clicked.connect(self.show_filter_dialog)

        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addWidget(self.filter_button)
        toolbar_layout.addStretch()

        # 筛选信息
        self.filter_info = QLabel("未应用筛选")
        toolbar_layout.addWidget(self.filter_info)

        main_layout.addLayout(toolbar_layout)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Vertical)

        # 上部图表区域
        chart_frame = QFrame()
        chart_layout = QVBoxLayout()

        # 图表类型选择
        chart_type_layout = QHBoxLayout()
        chart_type_layout.addWidget(QLabel("图表类型:"))

        self.chart_type_input = QComboBox()
        self.chart_type_input.addItems(["饼图", "柱状图"])
        self.chart_type_input.currentTextChanged.connect(self.update_chart)
        chart_type_layout.addWidget(self.chart_type_input)

        chart_type_layout.addStretch()
        chart_layout.addLayout(chart_type_layout)

        # 图表视图
        self.chart_view = QChartView()
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        chart_layout.addWidget(self.chart_view)

        chart_frame.setLayout(chart_layout)

        # 下部数据表格
        table_frame = QFrame()
        table_layout = QVBoxLayout()

        # 贡献统计表格
        self.contribution_table = QTableWidget()
        self.contribution_table.setColumnCount(6)
        self.contribution_table.setHorizontalHeaderLabels([
            "用户", "语言", "翻译数", "审核数", "修改数", "总贡献"
        ])
        self.contribution_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.contribution_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        table_layout.addWidget(self.contribution_table)
        table_frame.setLayout(table_layout)

        splitter.addWidget(chart_frame)
        splitter.addWidget(table_frame)
        splitter.setSizes([300, 300])

        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    def set_project_id(self, project_id):
        """设置项目ID"""
        self.project_id = project_id
        self.load_contributions()

    def show_filter_dialog(self):
        """显示筛选对话框"""
        dialog = ContributionFilterDialog(self)
        if dialog.exec():
            self.filter_options = dialog.get_filter_options()
            self.update_filter_info()
            self.load_contributions()

    def update_filter_info(self):
        """更新筛选信息显示"""
        info_parts = []

        if self.filter_options.get("user_id"):
            info_parts.append(f"用户ID: {self.filter_options['user_id']}")

        if self.filter_options.get("since"):
            since_date = QDateTime.fromMSecsSinceEpoch(self.filter_options['since']).toString(Qt.DateFormat.ISODate)
            info_parts.append(f"起始日期: {since_date}")

        if self.filter_options.get("utils"):
            until_date = QDateTime.fromMSecsSinceEpoch(self.filter_options['utils']).toString(Qt.DateFormat.ISODate)
            info_parts.append(f"结束日期: {until_date}")

        if self.filter_options.get("lang"):
            info_parts.append(f"语言: {self.filter_options['lang']}")

        if info_parts:
            self.filter_info.setText(" | ".join(info_parts))
        else:
            self.filter_info.setText("未应用筛选")

    def load_contributions(self):
        """加载贡献统计"""
        if not self.project_id:
            return

        try:
            self.contributions = self.api.get_contributions(
                self.project_id,
                user_id=self.filter_options.get("user_id"),
                since=self.filter_options.get("since"),
                until=self.filter_options.get("utils"),
                lang=self.filter_options.get("lang")
            )

            self.update_contribution_table()
            self.update_chart()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载贡献统计失败: {str(e)}")

    def update_contribution_table(self):
        """更新贡献统计表格"""
        self.contribution_table.setRowCount(len(self.contributions))

        for row, contribution in enumerate(self.contributions):
            # 用户
            user = contribution.get("user", {})
            username = user.get("username", "")
            user_item = QTableWidgetItem(username)
            self.contribution_table.setItem(row, 0, user_item)

            # 语言
            lang = contribution.get("lang", "")
            lang_item = QTableWidgetItem(lang)
            self.contribution_table.setItem(row, 1, lang_item)

            # 翻译数
            translated = contribution.get("translated", 0)
            translated_item = QTableWidgetItem(str(translated))
            self.contribution_table.setItem(row, 2, translated_item)

            # 审核数
            reviewed = contribution.get("reviewed", 0)
            reviewed_item = QTableWidgetItem(str(reviewed))
            self.contribution_table.setItem(row, 3, reviewed_item)

            # 修改数
            modified = contribution.get("modified", 0)
            modified_item = QTableWidgetItem(str(modified))
            self.contribution_table.setItem(row, 4, modified_item)

            # 总贡献
            total = translated + reviewed + modified
            total_item = QTableWidgetItem(str(total))
            self.contribution_table.setItem(row, 5, total_item)

            # 存储完整贡献数据
            self.contribution_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, contribution)

    def update_chart(self):
        """更新图表"""
        if not self.contributions:
            return

        chart_type = self.chart_type_input.currentText()

        if chart_type == "饼图":
            self.create_pie_chart()
        elif chart_type == "柱状图":
            self.create_bar_chart()

    def create_pie_chart(self):
        """创建饼图"""
        # 按用户汇总贡献
        user_contributions = {}

        for contribution in self.contributions:
            user = contribution.get("user", {})
            username = user.get("username", "未知用户")

            if username not in user_contributions:
                user_contributions[username] = 0

            translated = contribution.get("translated", 0)
            reviewed = contribution.get("reviewed", 0)
            modified = contribution.get("modified", 0)

            user_contributions[username] += translated + reviewed + modified

        # 创建饼图
        series = QPieSeries()

        for username, count in user_contributions.items():
            series.append(f"{username} ({count})", count)

        # 创建图表
        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("用户贡献分布")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)

        self.chart_view.setChart(chart)

    def create_bar_chart(self):
        """创建柱状图"""
        # 按用户汇总各类贡献
        user_translated = {}
        user_reviewed = {}
        user_modified = {}

        for contribution in self.contributions:
            user = contribution.get("user", {})
            username = user.get("username", "未知用户")

            if username not in user_translated:
                user_translated[username] = 0
                user_reviewed[username] = 0
                user_modified[username] = 0

            user_translated[username] += contribution.get("translated", 0)
            user_reviewed[username] += contribution.get("reviewed", 0)
            user_modified[username] += contribution.get("modified", 0)

        # 创建柱状图
        set_translated = QBarSet("翻译")
        set_reviewed = QBarSet("审核")
        set_modified = QBarSet("修改")

        categories = []

        for username in user_translated.keys():
            categories.append(username)
            set_translated.append(user_translated[username])
            set_reviewed.append(user_reviewed[username])
            set_modified.append(user_modified[username])

        series = QBarSeries()
        series.append(set_translated)
        series.append(set_reviewed)
        series.append(set_modified)

        # 创建图表
        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("用户贡献分类统计")
        chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)

        # 设置坐标轴
        axis_x = QValueAxis()
        axis_x.append(range(len(categories)))
        axis_x.setTickCount(len(categories))
        axis_x.setLabelsVisible(False)

        axis_y = QValueAxis()
        axis_y.setTickCount(10)

        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)

        series.attachAxis(axis_x)
        series.attachAxis(axis_y)

        # 设置图例
        legend = chart.legend()
        legend.setVisible(True)
        legend.setAlignment(Qt.AlignmentFlag.AlignBottom)

        self.chart_view.setChart(chart)
