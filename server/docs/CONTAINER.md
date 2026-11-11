# 可选容器运行方案

此配置用于把本地开发命令放在容器中执行。**本次没有 Docker 运行环境，镜像构建和容器内执行尚未实测。**先按 `VALIDATION.md` 验证范围理解此模板，使用前在你的机器构建并完成下面的验收。

## 与直接在本机运行的区别

本机模式可以直接使用电脑上的 Node、Java、Python 等工具链。容器模式使用容器内的工具链，项目文件通过显式挂载进入容器。本例只包含 Python、Git 和系统 shell，Node/JDK/编译器需根据项目加入 Dockerfile 后重建。容器内不能自动访问宿主 `localhost` 的数据库或开发服务。

这里默认启用项目写入和命令执行，所以挂载目录内的修改会回写到电脑。建议先使用独立 Git worktree 或项目副本。容器是一个隔离边界，不能宣传为能防御所有恶意程序的绝对安全环境。

## 构建

在源码根目录执行：

```bash
docker build -t sugar-local-mcp:0.5.0 .
```

Linux 的挂载目录若不属于 UID/GID 1000，可以用当前普通开发用户的 UID/GID 重建：

```bash
docker build \
  --build-arg BRIDGE_UID="$(id -u)" \
  --build-arg BRIDGE_GID="$(id -g)" \
  -t sugar-local-mcp:0.5.0 .
```

不要用 root 账户运行上面的自定义 UID/GID 构建。macOS/Windows 的 Docker Desktop 挂载权限行为不同，先用下文自检确认容器能读写项目。镜像内的服务账号不是 root。

## 创建启动脚本

将以下脚本保存到源码与项目之外的固定路径，例如 `/Users/you/bin/start-sugar-mcp-container.sh`。将 `/absolute/path/to/project-copy` 替换成已经存在的工作副本绝对路径。

```sh
#!/bin/sh
set -eu
exec docker run --rm -i --init \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --pids-limit=256 \
  --memory=4g \
  --cpus=2 \
  --network=none \
  --tmpfs=/tmp:rw,nosuid,nodev,size=512m,mode=1777 \
  --tmpfs=/scratch:rw,nosuid,nodev,size=512m,mode=1777 \
  --mount=type=bind,src=/absolute/path/to/project-copy,dst=/workspaces/project \
  --mount=type=volume,src=sugar-local-mcp-state,dst=/state \
  sugar-local-mcp:0.5.0
```

```bash
chmod 700 /Users/you/bin/start-sugar-mcp-container.sh
```

这里的 `-i` 保持 MCP stdio 管道，**不要加 `-t`**。只挂载项目工作副本和备份状态卷；不挂载宿主主目录、SSH agent 或 Docker socket。隧道客户端运行在宿主机，控制面的 key 不需要传入容器。镜像与 `/etc/sugar-local-mcp.json` 在只读根文件系统内。

`--network=none` 禁止容器联网。需要联网安装依赖时，可以预先构建带依赖的项目镜像；或者由操作者明确选择受控联网配置。直接删除这项会恢复 Docker 默认网络，不能再宣称限制了网络访问。要访问本机数据库，需另行规划网络并按实际权限测试。

备份卷持久保留。它不是自动清理的缓存，需自己定期维护备份容量。首次创建的新命名卷会从镜像中的 `/state` 初始化；若重建改变 UID/GID，旧卷的所有权可能需要由操作者修正。

## 隧道配置

按 `CONNECT_CHATGPT.md` 创建并授权隧道，把 `--mcp-command` 改成这个启动脚本的绝对路径。例如：

```bash
tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile sugar-container \
  --tunnel-id tunnel_替换成真实ID \
  --mcp-command "/Users/you/bin/start-sugar-mcp-container.sh"
```

然后使用 `sugar-local-mcp tunnel --profile sugar-container` 启动并在本地隐藏输入 runtime key。配置只要完成一次，后续仍是一条启动命令。Docker 容器是该进程树中的 MCP 服务端。

## 容器上线前验收

先通过本地 MCP 客户端或 ChatGPT 进行这些检查：

1. `workspace_list` 只列出 `/workspaces/project`。
2. `read_file` 能读取工作副本中的一个测试文件。
3. 在工作副本中创建一个临时文件并确认宿主同步出现；确认备份能写到 `/state`。
4. `run_command` 能执行镜像内的 Python 测试，`read_process` 能读完输出与退出码。
5. 容器内不能读到未挂载的宿主个人文件；默认无法联网。
6. 停止隧道后容器及其子进程退出，没有遗留 `docker run` 容器。

只有这些检查实际完成，才能把容器链路标记为已验证。公开 HTTPS 入口、OAuth 和浏览器预览均不在这个模板中。
