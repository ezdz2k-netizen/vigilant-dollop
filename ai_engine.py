from openai import OpenAI

client = OpenAI()

response = client.responses.create(
    model="gpt-6.1-sol",
    reasoning={"effort": "medium"},
    input="Reply with exactly: MT5 AI connection successful."
)

print(response.output_text)