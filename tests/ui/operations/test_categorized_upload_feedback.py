from transbridge.ui.operations.categorized_upload_feedback import UploadProgress


def test_gateway_stage_keeps_executor_counts_and_initial_unknown_total():
    events = []
    progress = UploadProgress(lambda *args: events.append(args))
    progress.status("读取云端")
    progress(7, 20_000, "已处理文件")
    progress.status("上传分类文件")
    progress.status("回读核实")
    assert events == [
        (0, 0, "读取云端"),
        (7, 20_000, "已处理文件"),
        (7, 20_000, "上传分类文件"),
        (7, 20_000, "回读核实"),
    ]
