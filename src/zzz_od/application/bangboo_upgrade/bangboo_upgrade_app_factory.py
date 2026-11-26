from __future__ import annotations

from typing import TYPE_CHECKING

from one_dragon.base.operation.application.application_config import ApplicationConfig
from one_dragon.base.operation.application.application_factory import ApplicationFactory
from one_dragon.base.operation.application_base import Application
from zzz_od.application.bangboo_upgrade import bangboo_upgrade_const
from zzz_od.application.bangboo_upgrade.bangboo_upgrade_app import BangbooUpgradeApp
from zzz_od.application.bangboo_upgrade.bangboo_upgrade_config import BangbooUpgradeConfig

if TYPE_CHECKING:
    from zzz_od.context.zzz_context import ZContext


class BangbooUpgradeAppFactory(ApplicationFactory):

    def __init__(self, ctx: ZContext) -> None:
        ApplicationFactory.__init__(self, bangboo_upgrade_const)
        self.ctx: ZContext = ctx

    def create_application(self, instance_idx: int, group_id: str) -> Application:
        return BangbooUpgradeApp(self.ctx)

    def create_config(self, instance_idx: int, group_id: str) -> ApplicationConfig:
        return BangbooUpgradeConfig(instance_idx=instance_idx, group_id=group_id)
