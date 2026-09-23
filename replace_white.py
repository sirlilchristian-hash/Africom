import re

with open("app/src/main/java/com/example/ui/screens/FaceOffArenaScreen.kt", "r") as f:
    lines = f.readlines()

for i in range(len(lines)):
    if "Color.White" in lines[i] and i > 150:
        lines[i] = lines[i].replace("Color.White", "Color(0xFF0F172A)")

with open("app/src/main/java/com/example/ui/screens/FaceOffArenaScreen.kt", "w") as f:
    f.writelines(lines)
