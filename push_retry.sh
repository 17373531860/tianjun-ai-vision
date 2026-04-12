#!/bin/bash
echo "Setting repo public..."
while ! gh repo edit --visibility public --accept-visibility-change-consequences; do
    echo "Retrying gh repo edit..."
    sleep 3
done

echo "Pushing main branch..."
while ! git push origin main; do
    echo "Retrying git push main..."
    sleep 3
done

git tag -f v2.4.0
echo "Pushing tag..."
while ! git push -f origin v2.4.0; do
    echo "Retrying git push tag..."
    sleep 3
done
echo "Push successful!"
