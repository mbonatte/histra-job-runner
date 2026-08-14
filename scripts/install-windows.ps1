$ErrorActionPreference = "Stop"

$RunnerRoot = "C:\HiStrA-Runner"
New-Item -ItemType Directory -Force -Path $RunnerRoot | Out-Null

py -3.12 -m venv (Join-Path $RunnerRoot ".venv")
$Python = Join-Path $RunnerRoot ".venv\Scripts\python.exe"

& $Python -m pip install --upgrade pip
& $Python -m pip install "git+https://github.com/mbonatte/histra-job-runner.git@v1.1.0"
& $Python -c "import histra, histra_runner; print('runner', histra_runner.__version__, 'histra-python', histra.__version__)"
