Set-Location "c:\Users\mpandav\Downloads\Work\TIBCO\git\flogo-agent-studio"

$apps = @(
    @{ exe = "bin\apps\rule-engine-service.exe";    out = "logs\rule-engine.log";    err = "logs\rule-engine-err.log"    },
    @{ exe = "bin\apps\design-service.exe";         out = "logs\design-service.log"; err = "logs\design-service-err.log" },
    @{ exe = "bin\apps\deploy-service.exe";         out = "logs\deploy-service.log"; err = "logs\deploy-service-err.log" },
    @{ exe = "bin\apps\agent-chat-service.exe";     out = "logs\agent-chat.log";     err = "logs\agent-chat-err.log"     },
    @{ exe = "bin\apps\ingestion-service.exe";      out = "logs\ingestion.log";      err = "logs\ingestion-err.log"      },
    @{ exe = "bin\apps\feedback-service.exe";       out = "logs\feedback.log";       err = "logs\feedback-err.log"       },
    @{ exe = "bin\apps\config-service.exe";         out = "logs\config.log";         err = "logs\config-err.log"         },
    @{ exe = "bin\apps\sse-stream-service.exe";     out = "logs\sse-stream.log";     err = "logs\sse-stream-err.log"     },
    @{ exe = "bin\apps\agent-builder-service.exe";  out = "logs\agent-builder.log";  err = "logs\agent-builder-err.log"  },
    @{ exe = "bin\apps\mcp-server.exe";             out = "logs\mcp-server.log";     err = "logs\mcp-server-err.log"     }
)

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

foreach ($app in $apps) {
    Start-Process -FilePath $app.exe `
        -RedirectStandardOutput $app.out `
        -RedirectStandardError  $app.err `
        -WindowStyle Hidden
    Write-Host "Started: $($app.exe)"
    Start-Sleep -Milliseconds 200
}

Write-Host ""
Write-Host "All services started. Waiting 5s for readiness..."
Start-Sleep -Seconds 5
Write-Host "Ready."
