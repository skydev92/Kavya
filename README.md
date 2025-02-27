# Kavya

**Craft Your Story with AI Elegance**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1.0-brightgreen.svg)]()
[![Build Status](https://img.shields.io/badge/build-passing-success.svg)]()

## Table of Contents

- [Introduction](#introduction)
- [Features](#features)
- [Demo](#demo)
- [Installation](#installation)
- [Usage](#usage)
- [Advanced Features](#advanced-features)
- [Security and Compliance](#security-and-compliance)
- [API Documentation](#api-documentation)
- [Configuration](#configuration)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)
- [Database Setup](#database-setup)

---

## Introduction

**Kavya** is an AI-powered content authoring assistant designed to transform your ideas into compelling web content effortlessly. By blending creativity with cutting-edge AI technology, Kavya empowers writers, marketers, and creators to produce high-quality content with ease and efficiency.

---

## Features

- **AI-Assisted Writing**: Generate drafts, suggestions, and complete articles.
- **Advanced Prompt Optimization**: Utilize RouteLLM and LangChain to optimize prompts and create advanced multi-model, multi-prompt sequences.
- **Content Enhancement**: Automatically improve content quality, style, and coherence.
- **Content Optimization**: Improve SEO and readability.
- **Multi-language Support**: Write and translate content in multiple languages.
- **Tone and Style Customization**: Match your brand voice.
- **Collaboration Tools**: Work with team members in real-time.
- **CMS Integration**: Seamlessly integrate with platforms like WordPress and Drupal.
- **Template Library**: Access a variety of content templates for blogs, ads, emails, and more.
- **Plagiarism Checker**: Ensure your content is original.
- **Version Control**: Track changes and revert to previous versions.
- **Export Options**: Download content in various formats (PDF, DOCX, HTML).

---

## Installation

### Prerequisites

- **Operating System**: Windows, macOS, or Linux
- **Python**: 3.11 or higher
- **Docker**: Latest stable version
- **PostgreSQL**: 17 or higher (for development)

### Steps to Install and Run Kavya

1. **Clone the Repository**
   ```bash
   git clone https://github.com/your-username/kavya.git
   cd kavya
   ```

2. **Set Up Development Environment**

   a. **Install PostgreSQL** (macOS)
   ```bash
   # Install PostgreSQL 17
   brew install postgresql@17
   
   # Start PostgreSQL service
   brew services start postgresql@17
   
   # Verify installation
   postgres --version
   ```

   b. **Set Up Local Database**
   ```bash
   # Run the database setup script
   python scripts/setup_local_db.py
   
   # This will:
   # - Create a database user (kavya_user)
   # - Create a database (kavya_db)
   # - Set up the password (postgres)
   # - Create/update your .env file
   ```

   c. **Test Database Connection**
   ```bash
   # Connect to the database
   psql -h localhost -U kavya_user -d kavya_db
   # When prompted for password, enter: postgres
   
   # You should see the PostgreSQL prompt:
   kavya_db=>
   # Type \q to exit
   ```

3. **Run Kavya**

   **Development Mode** (with local PostgreSQL)
   ```bash
   # First ensure PostgreSQL is running
   brew services start postgresql@17

   # Run with local source code mounting for fast development
   docker run --rm -it \
     --name kavya-dev \
     -p 8089:${PORT:-8080} \
     -v $(pwd)/routellm:/app/routellm \
     --env-file .env \
     -e ENVIRONMENT=dev \
     kavya

   # Now you can:
   # - Edit Python files locally
   # - Ctrl+C to stop the container
   # - Run the same command to restart with new code
   # - No rebuild needed for Python code changes
   # - Container will be named 'kavya-dev' for easy reference
   ```

   **Production Mode** (with Google Cloud SQL)
   
   Without Docker (Direct Python):
   ```bash
   # First start the Cloud SQL Proxy in the background
   ./cloud-sql-proxy kavya-437313:europe-west4:kavya &

   # Then load production environment variables and start server
   export $(grep -v '^#' .env.prod | xargs) && python -m routellm.openai_server --verbose --routers mf --config config.yaml --port 8089


   # Required environment variables in .env.prod:
   # - INSTANCE_CONNECTION_NAME=<project>:<region>:<instance>
   # - DB_USER=postgres
   # - DB_PASS=<database-password>
   # - DB_NAME=postgres
   # - JWT_PUBLIC_KEY_B64=<base64-encoded-public-key>
   ```

   With Docker:
   ```bash
   # Run without source mounting, using Google Cloud SQL
   docker run -d \
     --name kavya-prod \
     -p 8080:${PORT:-8080} \
     --env-file .env.prod \
     kavya

   # Required environment variables for production:
   # - INSTANCE_CONNECTION_NAME=<project>:<region>:<instance>
   # - DB_PASS=<database-password>
   # 
   # Optional environment variables (with defaults):
   # - DB_USER=kavya
   # - DB_NAME=kavya
   # - DB_SOCKET_DIR=/cloudsql
   ```

   **Useful Docker Commands**
   ```bash
   # View logs
   docker logs kavya-dev    # For development
   docker logs kavya-prod   # For production

   # Stop container
   docker stop kavya-dev    # For development
   docker stop kavya-prod   # For production

   # Remove container
   docker rm kavya-dev     # For development
   docker rm kavya-prod    # For production
   ```

   **When to Rebuild Docker Image**
   - Changes to `requirements.txt`
   - Changes to `Dockerfile`
   - Changes to files outside `routellm/` directory

4. **Access Kavya**
   - Development: `http://localhost:8089`
   - Production: `http://localhost:8080`

### Alternative: Running Without Docker

If you prefer not to use Docker, you can set up a virtual environment and run Kavya directly:

1. **Create and Activate a Virtual Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set Up Database** (if not done already)
   ```bash
   python scripts/setup_local_db.py
   ```

4. **Run Kavya**
   ```bash
   python -m routellm.openai_server --verbose --routers mf --config config.yaml --port 8089
   ```

5. **Access Kavya**
   Open your web browser and navigate to `http://localhost:8080` to start using Kavya.

### Troubleshooting

If you encounter database issues:

1. **Check PostgreSQL Service**
   ```bash
   brew services list          # Check if PostgreSQL is running
   brew services start postgresql@17  # Start if not running
   ```

2. **Test Connection**
   ```bash
   psql -h localhost -U kavya_user -d kavya_db
   # Password: postgres
   ```

3. **Common Solutions**
   - Ensure PostgreSQL service is running
   - Verify your `.env` file has the correct `DATABASE_URL`
   - For production, check that Cloud SQL Proxy is running

---

## Advanced Features

### Multi-Model Multi-Prompt Sequences

Kavya leverages **RouteLLM** and **LangChain** to create advanced multi-model, multi-prompt sequences. This allows for:

- **Dynamic Prompt Routing**: Automatically select the most suitable AI model based on the task.
- **Chained Prompts**: Break down complex tasks into manageable steps, improving output quality.
- **Context Preservation**: Maintain context across multiple prompts for coherent content generation.
- **Custom Workflows**: Design custom sequences tailored to your specific content creation needs.

### How to Use Advanced Features

1. **Enable Advanced Mode**

   - In your project settings, toggle on **Advanced Mode**.

2. **Configure Your Workflow**

   - Use the visual editor to arrange and configure prompt sequences.
   - Select from available AI models and tools.

3. **Generate Content**

   - Run the workflow to generate content.
   - Review and adjust as needed.

---

## Security and Compliance

### European Hosting

- **Data Centers**: All data is stored and processed on servers located within the European Union.
- **Data Residency**: Ensures compliance with local data residency requirements.

### EU Data Security and Privacy Standards

- **GDPR Compliance**: Kavya adheres to the General Data Protection Regulation (GDPR), ensuring user data is handled with the highest level of privacy and security.
- **Data Encryption**: All data in transit and at rest is encrypted using industry-standard encryption protocols.
- **Access Controls**: Strict access controls are in place to prevent unauthorized access to user data.

### Regular Audits

- **Security Audits**: Regular third-party security assessments are conducted to ensure compliance and identify potential vulnerabilities.
- **Compliance Certifications**: Working towards obtaining relevant certifications like ISO 27001.

---

## API Documentation

Integrate Kavya's capabilities into your own applications using our RESTful API.

### Authentication

Authenticate using API keys:

- Obtain your API key from your account dashboard.
- Include it in your request headers.

```http
Authorization: Bearer YOUR_API_KEY
```

### Endpoints

- **Generate Content**

  ```http
  POST /v1/chat/completions
  ```

  Generate content with optional token chunking to control text editor refresh rate:

  ```json
  {
    "model": "gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Write an article about AI"
      }
    ],
    "stream": true,
    "chunking": {
      "chunk_size": 3
    }
  }
  ```

  Parameters:
  - `model`: The model to use for generation
  - `messages`: Array of message objects with role and content
  - `stream`: Boolean to enable streaming responses
  - `chunking`: (Optional) Configuration for token chunking
    - `chunk_size`: Number of tokens to accumulate before sending (default: 3)

  The chunking configuration helps reduce text editor refresh rate by accumulating tokens before sending them to the client. A larger chunk size means fewer but larger updates, while a smaller size provides more granular updates.

## Database Setup

### Development Environment

The development environment uses a local PostgreSQL database. Setup is automated:

1. **Install PostgreSQL** (if not already installed):
   ```bash
   # macOS
   brew install postgresql@17
   brew services start postgresql@17
   
   # Ubuntu/Debian
   sudo apt-get update
   sudo apt-get install postgresql-15
   ```

2. **Run the Setup Script**
   ```bash
   python scripts/setup_local_db.py
   ```
   This script will:
   - Create the database user and database
   - Set up the correct permissions
   - Create/update your `.env` file with the correct configuration
   - Test the connection

3. **Verify Setup**
   ```bash
   # Test database connection
   psql -h localhost -U kavya_user -d kavya_db
   # Password: postgres
   ```

### Production Environment

The production environment uses Google Cloud SQL. Required configuration in `.env.prod`:

```bash
ENVIRONMENT=prod
INSTANCE_CONNECTION_NAME=your-project:region:instance
DATABASE_URL=postgresql://kavya_user:your-prod-password@localhost:5432/kavya_db
PRIVATE_IP=true  # Optional, set to true if using private IP
```

Note: `JWT_PUBLIC_KEY_B64` is required for both development and production environments.

### Troubleshooting

If you encounter database issues:

1. **Check PostgreSQL Service**
   ```bash
   brew services list          # Check if PostgreSQL is running
   brew services start postgresql@17  # Start if not running
   ```

2. **Test Connection**
   ```bash
   psql -h localhost -U kavya_user -d kavya_db
   # Password: postgres
   ```

3. **Common Solutions**
   - Ensure PostgreSQL service is running
   - Verify your `.env` file has the correct `DATABASE_URL`
   - For production, check that Cloud SQL Proxy is running

---

## Configuration

### Environment Variables

- **ENVIRONMENT**: Set to `dev` for development or `prod` for production.
- **INSTANCE_CONNECTION_NAME**: The connection name for Google Cloud SQL.
- **DB_USER**: The database user.
- **DB_PASS**: The database password.
- **DB_NAME**: The database name.
- **PRIVATE_IP**: Optional, set to true if using private IP (default: false).

### Configuration File

The configuration file is located at `config.yaml`. You can customize the following settings:

- **routers**: The router to use for content generation.
- **strong-model**: The powerful model for complex tasks.
- **weak-model**: The faster model for simpler tasks.
- **config**: Additional router-specific configuration.

---

## Contributing

We welcome contributions from the community. Please read our [Contribution Guidelines](CONTRIBUTING.md) for more information on how to contribute to Kavya.

---

## License

Kavya is licensed under the MIT License. See [LICENSE](LICENSE) for more information.

---

## Contact

For any questions or support, please contact us at [contact@kavya.com](mailto:contact@kavya.com).

---