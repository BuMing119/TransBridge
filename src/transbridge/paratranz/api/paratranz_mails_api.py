from transbridge.paratranz.paratranz_client import ParatranzClient


class ParatranzMailsAPI(ParatranzClient):
    def list_mails(self, page: int = 1, page_size: int = 50):
        """获取私信列表（分页）"""
        return self._request("GET", "/mails", params={"page": page, "pageSize": page_size})

    def send_mail(self, to: int, content: str):
        """
        发送私信

        Args:
            to: 接收者用户 ID
            content: 私信内容（支持 Markdown）
        """
        return self._request("POST", "/mails", json={"to": to, "content": content})

    def get_conversation(self, user_id: int):
        """
        获取与某用户的对话记录（按时间排列）

        Returns:
            Mail 数组，每项含 id, createdAt, from, to, content, html, status（0=未读, 1=已读）
        """
        return self._request("GET", f"/mails/conversations/{user_id}")
