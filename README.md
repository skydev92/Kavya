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

## Demo

Experience Kavya firsthand by trying our live demo:

[**Live Demo**](https://www.kavya.ai/demo)

![Kavya Interface Screenshot](docs/images/kavya_screenshot.png)

---

## Installation

### Prerequisites

- **Operating System**: Windows, macOS, or Linux
- **Python**: 3.8 or higher
- **Node.js**: v14.x or higher
- **npm**: v6.x or higher
- **Git**: Latest version
- **Browser**: Latest version of Chrome, Firefox, or Edge

### Steps

1. **Clone the Repository**

   ```bash
   git clone https://github.com/dxpr/kavya.git
   cd kavya
   ```

2. **Install Backend Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Install Frontend Dependencies**

   ```bash
   cd frontend
   npm install
   cd ..
   ```

4. **Configure Environment Variables**

   Rename `.env.example` to `.env` and update the variables accordingly.

5. **Initialize the Database**

   ```bash
   python manage.py migrate
   ```

6. **Start the Application**

   - **Backend**:

     ```bash
     python manage.py runserver
     ```

   - **Frontend**:

     ```bash
     cd frontend
     npm start
     ```

7. **Access Kavya**

   Open your browser and navigate to `http://localhost:3000`.

---

## Usage

### Getting Started

1. **Register an Account**

   - Go to `http://localhost:3000/signup`.
   - Fill in your details and verify your email.

2. **Log In**

   - Access `http://localhost:3000/login`.
   - Enter your credentials.

3. **Create a New Project**

   - Click on **"New Project"**.
   - Choose a template or start from scratch.

4. **Use the AI Assistant**

   - Input your prompt or topic.
   - Select the desired tone and language.
   - Choose the content enhancement options, including advanced features powered by RouteLLM and LangChain.
   - Click **"Generate"** to receive AI-generated content.

5. **Edit and Optimize**

   - Use the built-in editor to make changes.
   - Utilize the SEO tools for optimization.

6. **Collaborate**

   - Invite team members via email.
   - Assign roles and permissions.

7. **Export or Publish**

   - Export your content in preferred formats.
   - Publish directly to your CMS if integrated.

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
- **Privacy Policy**: Our detailed [Privacy Policy](https://www.kavya.ai/privacy) outlines how we collect, use, and protect user information.

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
  POST /api/v1/generate
  ```

- **Advanced Content Generation**

  ```http
  POST /api/v1/generate/advanced
  ```

- **List Templates**

  ```http
  GET /api/v1/templates
  ```

- **Translate Content**

  ```http
  POST /api/v1/translate
  ```

### Example Advanced Generation Request

```bash
POST /api/v1/generate/advanced HTTP/1.1
Host: api.kavya.ai
Content-Type: application/json
Authorization: Bearer YOUR_API_KEY

{
  "prompt": "The future of renewable energy",
  "tone": "Insightful",
  "language": "en",
  "workflow": {
    "steps": [
      {
        "model": "gpt-4",
        "task": "Outline creation"
      },
      {
        "model": "gpt-4",
        "task": "Draft writing"
      },
      {
        "model": "text-enhancer",
        "task": "Style refinement"
      }
    ]
  }
}
```

### Example Response

```json
{
  "status": "success",
  "content": "Renewable energy is rapidly transforming the global energy landscape..."
}
```

For full API documentation, including advanced features, visit our [API Docs](https://www.kavya.ai/docs/api).

---

## Configuration

Customize Kavya by modifying the `.env` file:

```env
# Server Configuration
PORT=8000

# Database Configuration
DB_HOST=localhost
DB_PORT=5432
DB_NAME=kavya_db
DB_USER=your_db_user
DB_PASS=your_db_password

# AI Model Configuration
AI_MODEL_PROVIDER=openai
AI_API_KEY=your_openai_api_key
ROUTELLM_ENABLED=true
LANGCHAIN_ENABLED=true

# Security and Compliance
DATA_CENTER_LOCATION=EU
GDPR_COMPLIANCE=true

# Other Configurations
JWT_SECRET=your_jwt_secret
EMAIL_SERVICE=your_email_service_provider
```

---

## Contributing

We welcome contributions!

### How to Contribute

1. **Fork the Repository**

2. **Create a Branch**

   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **Make Changes**

   - Follow the existing code style.
   - Write unit tests for new features.
   - Ensure compliance with EU data security and privacy standards.

4. **Commit Changes**

   ```bash
   git commit -m "Add new feature: your feature name"
   ```

5. **Push to Your Fork**

   ```bash
   git push origin feature/your-feature-name
   ```

6. **Open a Pull Request**

   - Go to the original repository.
   - Click on **"New Pull Request"**.

### Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.

---

## License

This project is licensed under the terms of the [MIT License](LICENSE).

---

## Contact

For support, feedback, or inquiries:

- **Email**: [support@kavya.ai](mailto:support@kavya.ai)
- **GitHub Issues**: [Issue Tracker](https://github.com/dxpr/kavya/issues)
- **Twitter**: [@KavyaAI](https://twitter.com/KavyaAI)
- **LinkedIn**: [Kavya AI](https://www.linkedin.com/company/kavya-ai)

---

*Craft Your Story with AI Elegance*

---

This updated README reflects the inclusion of advanced features like RouteLLM and LangChain for prompt optimization and multi-model, multi-prompt sequences. It also highlights that Kavya is hosted in Europe and complies with EU data security and privacy standards, ensuring users' data is protected according to stringent regulations.
