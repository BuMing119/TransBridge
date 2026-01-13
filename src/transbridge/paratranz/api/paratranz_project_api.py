import json
from src.transbridge.paratranz.paratranz_client import ParatranzClient
from src.transbridge.paratranz.config_manager import ParatranzConfig

class ParatranzProjectAPI(ParatranzClient):

    def list_projects(self):
        """获取项目列表"""
        return self._request("GET", "/projects")

    def create_project(self, data: dict):
        """创建项目
        data 示例:
        {
            "name": "Test Project",
            "sourceLanguage": "en",
            "description": "My translation project"
        }
        """
        return self._request("POST", "/projects", data=json.dumps(data))

    def get_project(self, project_id: int):
        """获取项目信息"""
        return self._request("GET", f"/projects/{project_id}")

    def update_project(self, project_id: int, data: dict):
        """更新项目信息"""
        return self._request("PUT", f"/projects/{project_id}", data=json.dumps(data))

    def delete_project(self, project_id: int):
        """删除项目"""
        return self._request("DELETE", f"/projects/{project_id}")
