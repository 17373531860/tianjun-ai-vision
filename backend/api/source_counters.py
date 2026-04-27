"""Counters 组件 (v2.7.16 P7 第三刀, 组合优于继承)。

把项目计数器子系统从 VideoSourceManager 抽出, 形成独立组件:
  - 自持 counters dict (主状态)
  - 提供持久化 (json 文件) 和快照 (DB session.counters_snapshot)
  - 通过 host 反向引用读取 channel_id / project_config / current_session_id

数据所有权:
  - 旧: VSM.counters (字段) + 散落在 3 个 mixin 的方法
  - 新: Counters.counters (字段) + 集中在本组件的方法

公共 API (核心):
  persist()                : 写 json 持久化
  get_file_path()          : 返回 json 路径
  save_snapshot_to_db()    : 写 DB session.counters_snapshot

历史方法名兼容 (通过 VSM.__getattr__ 透明转发):
  _persist_counters       → persist
  _get_counter_file       → get_file_path
  _save_counters_snapshot → save_snapshot_to_db
"""
import os
import json as _json
from backend.core.config import DATA_DIR


class Counters:
    def __init__(self, host=None):
        self._host = host
        self.counters = {}  # {counter_name: int_value}

    # ===== 持久化 =====
    def get_file_path(self) -> str:
        """返回当前通道的计数器持久化文件路径"""
        host = self._host
        project_id = host.project_config.get('id') if host.project_config else None
        return os.path.join(
            DATA_DIR, 'counters',
            f'project_{project_id}_ch{host.channel_id}.json'
        )

    def persist(self):
        """将当前计数器值写到通道专属文件, 避免多通道竞争同一行"""
        host = self._host
        project_id = host.project_config.get('id') if host.project_config else None
        if not project_id or not self.counters:
            return
        try:
            path = self.get_file_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                _json.dump(self.counters, f, ensure_ascii=False)
        except Exception as e:
            print(f"[计数器持久化] ch{host.channel_id} 保存失败: {e}")

    def save_snapshot_to_db(self):
        """定期保存计数器快照到会话 (防止闪退丢失数据)"""
        # lazy import 避免循环引用
        from backend.models.models import DetectionSession
        host = self._host
        if not host.current_session_id or not self.counters:
            return
        try:
            db = host._get_db_session()
            session = db.query(DetectionSession).filter(
                DetectionSession.id == host.current_session_id
            ).first()
            if session:
                session.counters_snapshot = self.counters.copy()
                db.commit()
                print(f"[数据持久化] 计数器已保存: {self.counters}")
            db.close()
        except Exception as e:
            print(f"[数据持久化] 保存计数器失败: {e}")
