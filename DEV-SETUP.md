# Development Setup

This guide helps you set up the development environment for Kavya with automatic linting and testing on commit.

## Quick Setup

1. **Clone and navigate to the project:**
   ```bash
   cd kavya
   ```

2. **Set up the development environment:**
   ```bash
   ./setup-dev.sh
   ```
   
   This script will:
   - Create a virtual environment if needed
   - Install development dependencies (Black, isort, pre-commit)
   - Install main project dependencies
   - Set up pre-commit hooks
   - Test the configuration

3. **Activate the virtual environment** (if not already active):
   ```bash
   source venv/bin/activate
   ```

## Pre-commit Hooks

The project uses **intelligent pre-commit hooks** that automatically run on every commit with smart test selection based on what files you change:

### What runs automatically:

#### **🎯 Smart Test Selection**
The hook intelligently decides which tests to run based on your changes:

- **📄 Documentation only** (`.md`, `.txt`, etc.): 
  - ✅ Linting only, **no API tests** (saves time!)

- **📝 Python file changes**:
  - ✅ **Black** formatter (auto-fixes formatting)
  - ✅ **isort** import sorter (auto-fixes import order) 
  - ✅ **Core API tests**:
    - `streaming-completion.sh` - Tests streaming responses
    - `non-streaming-completion.sh` - Tests non-streaming responses  
    - `structured-completion.sh` - Tests structured output
    - `image-analysis.sh` - Tests image processing

- **🌐 Web search changes** (`web_search.py`):
  - ✅ Core tests + **specialized web search tests**:
    - `web-search-streaming.sh` - Tests web search with streaming
    - `web-search-non-streaming.sh` - Tests web search without streaming

- **🔄 Controller/Provider changes** (`controller.py`, `provider_chain.py`):
  - ✅ Core tests + **fallback/provider tests**:
    - `fallback-anthropic-openai.sh` - Tests provider fallbacks
    - `fallback-groq-openai.sh` - Tests provider switching
    - All `fallback-*.sh` tests (with 30s timeout)

- **⚙️ Configuration changes** (`.yaml`, `.json`, `.env`):
  - ✅ All tests run (config changes can affect API behavior)

#### **🔄 Auto-Environment Detection**
- ✅ **Automatically finds and activates** `venv/` or `.venv/` 
- ✅ **No manual activation needed** - hook handles it for you
- ✅ **Graceful fallback** when tools aren't available

### Hook Behavior:
- ✅ **Auto-fixes**: Formatting issues are automatically fixed and staged
- ✅ **Auto-commits**: Fixed files are automatically included in your commit
- ❌ **Blocks commits**: If tests fail or issues can't be auto-fixed
- 🚀 **Performance optimized**: Only runs relevant tests for your changes

## Manual Commands

### Linting:
```bash
# Format all Python files
python -m black .

# Sort all imports
python -m isort .

# Run all pre-commit hooks manually
pre-commit run --all-files

# Run hooks on specific files
pre-commit run --files kavya/models.py
```

### Testing:
```bash
# Run individual API tests
./tests/streaming-completion.sh
./tests/non-streaming-completion.sh
./tests/structured-completion.sh
./tests/image-analysis.sh

# Run all API tests
find tests -name "*.sh" -executable -exec {} \;
```

## Troubleshooting

### Hook not running:
```bash
# Check if hook is installed and executable
ls -la .git/hooks/pre-commit

# Reinstall hooks
pre-commit install --overwrite
```

### Missing tools:
```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Or install specific tools
pip install black==24.4.2 isort==5.13.2
```

### API tests failing:
```bash
# Make sure the server is running
python -m kavya.openai_server --port 8080

# Check server health
curl http://localhost:8080/health

# Run tests with verbose output
./tests/non-streaming-completion.sh -v
```

### Bypassing hooks (not recommended):
```bash
# Skip all hooks (only use for emergencies)
git commit --no-verify

# Skip specific hooks
SKIP=black,isort git commit
```

## Configuration Files

- **`.pre-commit-config.yaml`** - Pre-commit framework configuration
- **`.git/hooks/pre-commit`** - Manual pre-commit hook script
- **`requirements-dev.txt`** - Development dependencies
- **GitHub Actions** - `.github/workflows/lint.yml` uses same tools

## IDE Integration

### VS Code:
Install these extensions for the best experience:
- Python
- Black Formatter  
- isort

Add to your VS Code settings:
```json
{
    "python.formatting.provider": "black",
    "python.sortImports.provider": "isort",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.organizeImports": true
    }
}
```

## Workflow Examples

### Example 1: Documentation Changes (Fast)
```bash
# 1. Update documentation
vim README.md

# 2. Stage and commit
git add README.md
git commit -m "Update API documentation"

# The hook will:
# 🔄 Activate virtual environment automatically
# 📄 Detect only docs changed
# ⏭️ Skip all API tests (saves ~30 seconds!)
# ✅ Complete in ~1 second
```

### Example 2: Python Code Changes (Full Testing)
```bash
# 1. Make your changes  
vim kavya/models.py

# 2. Stage and commit
git add kavya/models.py
git commit -m "Add reasoning support to models"

# The hook will:
# 🔄 Activate virtual environment automatically
# 🖤 Check and fix formatting with Black
# 📦 Check and fix imports with isort  
# 🧪 Run core API tests (streaming, non-streaming, structured, image)
# ✅ Auto-stage any fixes
# ❌ Block commit if tests fail
```

### Example 3: Web Search Changes (Specialized Testing)
```bash
# 1. Modify web search logic
vim kavya/web_search.py

# 2. Stage and commit
git add kavya/web_search.py  
git commit -m "Improve web search query generation"

# The hook will:
# 🔄 Activate virtual environment automatically
# 🖤 Fix formatting issues
# 🧪 Run core API tests
# 🌐 PLUS run specialized web search tests
# ✅ Ensure web search functionality works end-to-end
```

### Example 4: Controller Changes (Fallback Testing)
```bash
# 1. Update provider fallback logic
vim kavya/controller.py

# 2. Stage and commit
git add kavya/controller.py
git commit -m "Improve provider fallback handling"

# The hook will:
# 🔄 Activate virtual environment automatically  
# 🖤 Fix formatting issues
# 🧪 Run core API tests
# 🔄 PLUS run all fallback tests (anthropic-openai, groq-openai, etc.)
# ✅ Verify provider switching works correctly
```

### Example 5: Config Changes (Full Testing)
```bash
# 1. Update configuration
vim config.yaml

# 2. Stage and commit
git add config.yaml
git commit -m "Add new model configurations"

# The hook will:
# ⚙️ Detect config changes
# 🧪 Run full test suite (config affects API behavior)
# ✅ Ensure all functionality still works with new config
```