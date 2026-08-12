Background
Solve Intelligence builds Al software to help patent attorneys write patents.
Our product is an in-browser document editor. It works just like Word but, under the hood, is powered by an Al copilot to help patent attorneys draft and review patents.
A patent is a legal document which, if granted, gives the inventor the exclusive commercial rights to an invention. Patents describe such things as what a technology is, how it works, and how it's made.
Here is a short "Patent Overview" introducing what a patent is and which sections it is made up of, which you may read if interested.


Task
You've been handed a project. The project, while ambitious, is still a work in progress. Your task is to take this piece of work and extend it with some features we'd like to see.
This project consists of:
- A React/Vite Frontend, which includes a simple document editor.
- A FastAPl backend, with an embedded in-memory SQLite database with pre-written patent Claims' sections.
Primary Objectives
The README contains 2 tasks. Please read these carefully and build a solution to sive them.
You can take any approach you like.
Getting Started
Download the above zip file containing the project that we've put together. You'll find more detailed instructions, and some tips to help you get started in the project READMEs.
We will send an OpenAl API key to your personal email - please keep this confidential. If it stops working, just let us know (it probably needs to be reset or topped up with $).
Make your code as clear as possible. We prefer code that reads easily, is maintainable, and is not overly complex. Writing tests for your solution is also highly recommended. Once you're finished with your submission, zip it up, and send it over for us to review.
If invited to the next stage, you will present your work to your Solve Intelligence interviewers and summarise what you've done, why you did what you did, and what you would plan to do in the future were you to have more time. We'll also ask you to make changes to the codebase in real-time, as a pair programming exercise.
Solve Intelligence Tech Stack
in case helpful for you to see how this task would fit in with the wider product at Solve Intelligence, here is a breakdown of our tech stack:

• Frontend
• TypeScript - a typed layer on top of regular JavaScript. The docs have various introductions to TypeScript based on the programming languages that you already know.
React - intro tutorial is actually really good
• TinyMCE 6 (in-browser document editor functionality - we don't like this)
• Deployed using AWS Amplify
• Backend
• Python
• Codecademy Python course
FastAP! - this documentation probably most similarly describes the structure of our code: https://fastapi.tiangolo.com/tutorial/sql-databases/ SQLAlchemy (ORM for our postgres database)
Large language models
OpenAl AP! + Anthropic AP!
AWS
We package our API as a docker image and push it to AWS ECR, we then have three instances (fargate tasks on AWS ECS) running behind a load balancer. Our PostgreSQL database is running on AWS RDS.
• We use Terraform and GitHub Actions for CI/CD
• Data storage
Postgres for storing data (user documents etc.)
S3 for storing images
SuperTokens for user authentication
• User monitoring
Datadog Posthog
We're providing the above breakdown for completeness. If you've used any of the above tools before, in your interview you might consider mentioning how you would hypothetically take what you've built and integrate it with our tech stack to give additional functionality. However, you are not expected to have used or know about all or any of the above - you can learn on the job!