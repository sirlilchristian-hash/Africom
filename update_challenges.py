import re

with open('app/src/main/java/com/example/ui/screens/ChallengesScreen.kt', 'r') as f:
    content = f.read()

# 1. Replace the start of LazyColumn with a Column wrapper
content = re.sub(
    r'        LazyColumn\(\s*modifier = Modifier\s*\.fillMaxSize\(\)\s*\.padding\(innerPadding\)\s*\.testTag\("challenges_screen"\),\s*contentPadding = PaddingValues\(bottom = 120\.dp\)\s*\) \{\s*// =========================================================================\s*// 1\. WAY ABOVE.*?item \{\s*Column\(',
    '''        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .testTag("challenges_screen_container")
        ) {
            // =========================================================================
            // 1. HEADER AND TABS
            // =========================================================================
            Column(''',
    content,
    flags=re.DOTALL
)

# 2. Add the 3rd tab to the Tabs row
tabs_search = r'(// TAB 2: Ai pools/connect.*?)\s*\}\s*\}\s*\}\s*// =========================================================================\s*// 2\. SEARCH ENGINE'
def tabs_replace(match):
    tab2 = match.group(1)
    return tab2 + '''
                        // TAB 3: My Bets & Duels
                        val isMyBetsSelected = selectedPoolSource == "my_bets"
                        Box(
                            modifier = Modifier
                                .weight(1f)
                                .clip(RoundedCornerShape(12.dp))
                                .background(
                                    if (isMyBetsSelected) FacelookBlue else androidx.compose.ui.graphics.Color.Transparent
                                )
                                .clickable {
                                    selectedPoolSource = "my_bets"
                                }
                                .padding(vertical = 10.dp)
                                .testTag("tab_my_bets"),
                            contentAlignment = androidx.compose.ui.Alignment.Center
                        ) {
                            Row(
                                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.Center
                            ) {
                                Icon(
                                    imageVector = androidx.compose.material.icons.Icons.Default.Whatshot,
                                    contentDescription = null,
                                    tint = if (isMyBetsSelected) androidx.compose.ui.graphics.Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.size(17.dp)
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = "My Bets",
                                    fontSize = 12.sp,
                                    fontWeight = if (isMyBetsSelected) FontWeight.Black else FontWeight.Bold,
                                    color = if (isMyBetsSelected) androidx.compose.ui.graphics.Color.White else MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                        }
                    }
                }

            // =========================================================================
            // CONTENT AREA (FaceOffArena OR Challenges List)
            // =========================================================================
            if (selectedPoolSource == "my_bets") {
                FaceOffArenaScreen(
                    battles = battles,
                    activeBattleIndex = activeBattleIndex,
                    leaderboardUsers = leaderboardUsers,
                    userProfile = userProfile,
                    onStakeBattle = onStakeBattle,
                    onNextBattle = onNextBattle,
                    onPrevBattle = onPrevBattle
                )
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize().weight(1f).testTag("challenges_screen_list"),
                    contentPadding = PaddingValues(bottom = 120.dp)
                ) {
            // =========================================================================
            // 2. SEARCH ENGINE'''

content = re.sub(tabs_search, tabs_replace, content, flags=re.DOTALL)

# 3. Add closing brace for the outer Column right before the Scaffold ends
# The Scaffold ends at 2548. Wait, we can just replace the end of Scaffold.
# The end of LazyColumn has a `}`. If we find the end of Scaffold, we just add one more `}`.
# Or better, we just add `}` before `    }` (which is the Scaffold closing block).
# Let's find the closing brace sequence for LazyColumn -> Scaffold -> ChallengesScreen.
# Wait, let's just do it directly.
content = re.sub(
    r'( {4}\}\s*\n {4}\}\n) {4}\}\n    // =========================================================================\n    // MATCH BET BOTTOM SHEET DIALOG',
    r'\1        }\n    }\n    // =========================================================================\n    // MATCH BET BOTTOM SHEET DIALOG',
    content
)

with open('app/src/main/java/com/example/ui/screens/ChallengesScreen.kt', 'w') as f:
    f.write(content)
