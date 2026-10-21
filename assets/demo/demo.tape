# Records assets/vayl-demo.gif from a real run. From the repo root, with OPENAI_API_KEY,
# LLM_PROVIDER=openai and OPENAI_MODEL exported and vayl-mcp on PATH:
#   vhs assets/demo/demo.tape
Output assets/vayl-demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1120
Set Height 720
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "source .venv/bin/activate && export PS1='$ ' && clear"
Enter
Show

Type "python assets/demo/clinical_demo.py"
Sleep 400ms
Enter
Wait+Screen@180s /# end of demo/
Sleep 4s
