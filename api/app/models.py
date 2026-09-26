"""数据模型与录入约束。

业务约束（与前端、README 保持一致）：
- 接续点 5~9 个，名称非空且唯一；
- 可用光纤 7~15 段，每段两个不同端点、整数长度 >= 1、整数衰减 >= 0；
- 主控室与展柜必须是已录入接续点且互不相同；
- 每路衰减上限为非负整数。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

MIN_NODES = 5
MAX_NODES = 9
MIN_FIBERS = 7
MAX_FIBERS = 15


class Fiber(BaseModel):
    a: str = Field(min_length=1, description="端点 A（接续点名称）")
    b: str = Field(min_length=1, description="端点 B（接续点名称）")
    length: int = Field(ge=1, description="光纤长度，正整数")
    attenuation: int = Field(ge=0, description="光纤衰减，非负整数")

    @model_validator(mode="after")
    def endpoints_differ(self) -> "Fiber":
        if self.a == self.b:
            raise ValueError("光纤两端不能是同一个接续点")
        return self


class Draft(BaseModel):
    nodes: list[str] = Field(min_length=MIN_NODES, max_length=MAX_NODES)
    fibers: list[Fiber] = Field(min_length=MIN_FIBERS, max_length=MAX_FIBERS)
    source: str = Field(min_length=1, description="主控室")
    target: str = Field(min_length=1, description="展柜")
    attenuation_limit: int = Field(ge=0, description="每路衰减上限")

    @field_validator("nodes")
    @classmethod
    def nodes_unique_and_nonblank(cls, v: list[str]) -> list[str]:
        if any(not n or not n.strip() for n in v):
            raise ValueError("接续点名称不能为空")
        if len(set(v)) != len(v):
            raise ValueError("接续点名称必须唯一")
        return v

    @model_validator(mode="after")
    def endpoints_known(self) -> "Draft":
        node_set = set(self.nodes)
        if self.source not in node_set or self.target not in node_set:
            raise ValueError("主控室与展柜必须是已录入的接续点")
        if self.source == self.target:
            raise ValueError("主控室与展柜不能是同一个接续点")
        for f in self.fibers:
            if f.a not in node_set or f.b not in node_set:
                raise ValueError("光纤端点必须是已录入的接续点")
        return self
