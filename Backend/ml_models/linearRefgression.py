from sklearn.tree import DecisionTreeClassifier

X = [
    [300, 0],
    [700, 1],
    [900, 1]
]

y = ["SAFE", "WARNING", "DRUNK"]

clf = DecisionTreeClassifier()
clf.fit(X, y)

print(clf.predict([[800, 1]]))