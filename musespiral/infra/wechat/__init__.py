"""微信基础设施子包（Phase C，正典 §八 Phase C / §二 infra/wechat/）。

- token：access_token 管理（稳定版接口 + 本地缓存）
- publisher：草稿/发表 API（draft/add、freepublish/submit）
- analytics：图文统计回填（datacube/getarticletotaldetail，F41 迁移自停维护的 getarticletotal）
- uploader：图片上传（正文 uploadimg / 封面 add_material + WeChatHost 适配壳）

旧路径 musespiral/token.py / musespiral/publisher.py / musespiral/analytics.py /
musespiral/uploader.py 为兼容垫片（Phase C 收尾删除），消费方 import 零改动。
"""
from .analytics import AnalyticsError, backfill_published_stats, fetch_article_stats, sync_reads
from .publisher import PublishError, check_publish_status, create_draft, submit_publish
from .token import TokenError, get_access_token
from .uploader import ImageUploader, UploadError, WeChatHost

__all__ = [
    "AnalyticsError",
    "ImageUploader",
    "backfill_published_stats",
    "PublishError",
    "TokenError",
    "UploadError",
    "WeChatHost",
    "check_publish_status",
    "create_draft",
    "fetch_article_stats",
    "get_access_token",
    "submit_publish",
    "sync_reads",
]
