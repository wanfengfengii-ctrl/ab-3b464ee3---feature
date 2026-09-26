FROM python:3.12-slim

WORKDIR /verify

# 运行测试与冒烟所需的依赖（含被测应用自身的依赖）
COPY api/requirements.txt api/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

# 被测代码与测试
COPY api/app ./app
COPY api/tests ./tests
COPY api/conftest.py ./
COPY verify/run.sh verify/smoke.py ./
RUN chmod +x run.sh

# 一次性服务：执行完 run.sh 后退出，退出码即验证结果
CMD ["./run.sh"]
