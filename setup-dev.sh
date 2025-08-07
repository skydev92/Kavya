#!/bin/bash

# Development environment setup script for Kavya
# This script sets up linting tools and pre-commit hooks

set -e

echo "🚀 Setting up Kavya development environment..."

# Check if we're in a virtual environment
if [ -z "$VIRTUAL_ENV" ]; then
    echo "⚠️  Not in a virtual environment. Creating one..."
    
    if [ ! -d "venv" ]; then
        echo "🔨 Creating virtual environment..."
        python3 -m venv venv
    fi
    
    echo "📁 To activate the virtual environment, run:"
    echo "  source venv/bin/activate"
    echo ""
    echo "Then run this script again:"
    echo "  ./setup-dev.sh"
    exit 0
fi

echo "✅ Virtual environment detected: $VIRTUAL_ENV"

# Install development dependencies
echo "📦 Installing development dependencies..."
pip install -r requirements-dev.txt

# Install main dependencies
echo "📦 Installing main dependencies..."
pip install -r requirements.txt

# Install pre-commit hooks using the framework (if available)
if command -v pre-commit >/dev/null 2>&1; then
    echo "🔗 Installing pre-commit hooks using pre-commit framework..."
    pre-commit install
    
    echo "🧪 Running pre-commit on all files to test setup..."
    pre-commit run --all-files || echo "⚠️  Some pre-commit checks failed, but setup is complete"
else
    echo "⚠️  pre-commit command not found, using manual hook only"
fi

# Test the smart pre-commit hook
echo "🧪 Testing smart pre-commit hook..."
if [ -x ".git/hooks/pre-commit" ]; then
    echo "✅ Smart pre-commit hook is installed and executable"
    
    # Test the hook with no staged files (should be fast)
    echo "🔍 Testing hook behavior with no staged files..."
    .git/hooks/pre-commit || echo "⚠️  Hook test completed with warnings"
    
else
    echo "❌ Smart pre-commit hook is not executable"
    chmod +x .git/hooks/pre-commit
fi

# Test linting tools
echo "🔍 Testing linting tools..."
echo "🖤 Black version:"
python3 -m black --version

echo "📦 isort version:"
python3 -m isort --version

# Test API (if server is running)
echo "🌐 Testing basic API connectivity..."
if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "✅ API server is running and reachable"
    
    # Run a quick test
    echo "🧪 Running a quick API test..."
    if [ -x "tests/non-streaming-completion.sh" ]; then
        tests/non-streaming-completion.sh || echo "⚠️  API test failed - check server status"
    fi
else
    echo "⚠️  API server not running on localhost:8080"
    echo "💡 Start the server with: python -m kavya.openai_server --port 8080"
fi

echo ""
echo "🎉 Smart pre-commit development environment setup complete!"
echo ""
echo "📋 Next steps:"
echo "  1. Make sure your API server is running: python -m kavya.openai_server --port 8080"
echo "  2. Make some changes and commit - the smart hook will run automatically!"
echo ""
echo "🎯 Smart Hook Features:"
echo "  - 📄 Documentation changes: Fast commits (no API tests)"
echo "  - 📝 Python changes: Full linting + core API tests"
echo "  - 🌐 Web search changes: + specialized web search tests"
echo "  - 🔄 Controller changes: + fallback/provider tests"
echo "  - ⚙️ Config changes: Full test suite"
echo "  - 🔄 Auto-activates your virtual environment"
echo ""
echo "🔧 Manual commands (if needed):"
echo "  - Format code: python -m black ."
echo "  - Sort imports: python -m isort ."
echo "  - Run all pre-commit hooks: pre-commit run --all-files"
echo "  - Test specific functionality: ./tests/streaming-completion.sh"
echo "  - Test web search: ./tests/web-search-streaming.sh"
echo ""
echo "💡 Pro tip: The hook automatically fixes formatting and includes fixes in your commit!"