on run
    set supportDirectory to (POSIX path of (path to application support folder from user domain)) & "Sugar Local MCP Desktop"
    try
        set sourceApp to POSIX path of (path to me)
        set sourceResources to sourceApp & "Contents/Resources/"
        set installedApp to do shell script "/bin/bash " & quoted form of (sourceResources & "install-app.sh") & " " & quoted form of sourceApp
        set resourcesDirectory to installedApp & "/Contents/Resources"
        set progress total steps to 3
        set progress completed steps to 1
        set progress description to "准备 Sugar Local MCP"
        set progress additional description to "首次运行会下载并校验必要组件，完成后显示连接配置。"
        with timeout of 1800 seconds
            set bootstrapOutput to do shell script "/bin/bash " & quoted form of (resourcesDirectory & "/bootstrap.sh") & " " & quoted form of resourcesDirectory & " " & quoted form of supportDirectory
        end timeout
        set pythonExecutable to last paragraph of bootstrapOutput
        set nativeArchitecture to do shell script "if [ \"$(/usr/sbin/sysctl -in hw.optional.arm64 2>/dev/null)\" = 1 ]; then /usr/bin/printf arm64; else /usr/bin/printf x86_64; fi"
        set progress completed steps to 3
        set progress total steps to 0
        with timeout of 3600 seconds
            do shell script "/usr/bin/arch -" & nativeArchitecture & " " & quoted form of pythonExecutable & " -B " & quoted form of (resourcesDirectory & "/desktop.py") & " --support " & quoted form of supportDirectory & " gui"
        end timeout
    on error errorText number errorNumber
        set progress total steps to 0
        if errorNumber is -128 then return
        set response to display dialog errorText with title "Sugar Local MCP · 安装准备" buttons {"关闭", "查看安装日志"} default button "关闭"
        if button returned of response is "查看安装日志" then
            try
                do shell script "/usr/bin/open " & quoted form of supportDirectory
            end try
        end if
    end try
end run
