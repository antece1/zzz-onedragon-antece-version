from __future__ import annotations

from typing import TYPE_CHECKING

from one_dragon.base.operation.application.application_config import ApplicationConfig
from one_dragon.base.operation.application.application_factory import ApplicationFactory
from one_dragon.base.operation.application_base import Application
from zzz_od.application.skill_upgrade import skill_upgrade_const
from zzz_od.application.skill_upgrade.skill_upgrade_flow_app import SkillUpgradeApp
from zzz_od.application.skill_upgrade.skill_upgrade_config import SkillUpgradeConfig

if TYPE_CHECKING:
    from zzz_od.context.zzz_context import ZContext


class SkillUpgradeAppFactory(ApplicationFactory):

    def __init__(self, ctx: ZContext) -> None:
        ApplicationFactory.__init__(self, skill_upgrade_const)
        self.ctx: ZContext = ctx

    def create_application(self, instance_idx: int, group_id: str) -> Application:
        return SkillUpgradeApp(self.ctx, group_id=group_id)

    def create_config(self, instance_idx: int, group_id: str) -> ApplicationConfig:
        return SkillUpgradeConfig(instance_idx=instance_idx, group_id=group_id)
