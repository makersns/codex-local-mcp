-- Compatibility launcher only. New releases build the SwiftUI application.
on run
    set nativeApp to (POSIX path of (path to home folder)) & "Applications/Codex Local MCP.app"
    try
        do shell script "/usr/bin/test -d " & quoted form of nativeApp
        do shell script "/usr/bin/open -n " & quoted form of nativeApp
    on error
        display dialog "请打开发布包中的 Codex Local MCP.app，或运行 Install_Codex_Local_MCP.command 完成安装。" with title "Codex Local MCP" buttons {"关闭"} default button "关闭"
    end try
end run
