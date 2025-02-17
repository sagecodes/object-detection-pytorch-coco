import torch
import torch.optim as optim
import torchvision
from torch.utils.data import DataLoader
from torchvision.datasets import CocoDetection
from torchvision.transforms import transforms as T
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from torchvision.ops import box_iou

# Check the available device
device = (
    "cuda"
    if torch.cuda.is_available()
    else "mps"
    if torch.backends.mps.is_available()
    else "cpu"
)
print(f"Using {device} device")

# Define the custom collate function
def collate_fn(batch):
    return tuple(zip(*batch))

# Define the transformations for the images
transform = T.Compose([T.ToTensor()])

# Load the dataset
dataset = CocoDetection(root='/content/geese-object-detection-dataset/', annFile='/content/geese-object-detection-dataset/train.json', transform=transform)
data_loader = DataLoader(dataset, batch_size=2, shuffle=True, num_workers=4, collate_fn=collate_fn)

# Load a pre-trained model
model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
model.to(device)

# Replace the classifier with a new one (number of classes + 1 for the background)
num_classes = 2  # Example: 1 class (goose) + background
in_features = model.roi_heads.box_predictor.cls_score.in_features
model.roi_heads.box_predictor = torchvision.models.detection.faster_rcnn.FastRCNNPredictor(in_features, num_classes)
model.to(device)

# Define optimizer and learning rate
params = [p for p in model.parameters() if p.requires_grad]
optimizer = optim.SGD(params, lr=0.005, momentum=0.9, weight_decay=0.0005)

# Learning rate scheduler
lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=3, gamma=0.1)

# Function to filter out and correct invalid boxes
def filter_and_correct_boxes(targets):
    filtered_targets = []
    for target in targets:
        boxes = target['boxes']
        labels = target['labels']
        valid_indices = []
        for i, box in enumerate(boxes):
            if box[2] > box[0] and box[3] > box[1]:
                valid_indices.append(i)
            else:
                print(f"Invalid box found and removed: {box}")
        filtered_boxes = boxes[valid_indices]
        filtered_labels = labels[valid_indices]
        filtered_targets.append({'boxes': filtered_boxes, 'labels': filtered_labels})
    return filtered_targets

# Function to evaluate the model
def evaluate_model(model, data_loader):
    model.eval()
    iou_list, loss_list = [], []
    correct_predictions, total_predictions = 0, 0
    with torch.no_grad():
        for images, targets in data_loader:
            images = [image.to(device) for image in images]
            targets = [{ 'boxes': torch.tensor([obj['bbox'] for obj in t], dtype=torch.float32).to(device),
                         'labels': torch.tensor([obj['category_id'] for obj in t], dtype=torch.int64).to(device)}
                       for t in targets]
            for target in targets:
                boxes = target['boxes']
                boxes[:, 2] += boxes[:, 0]
                boxes[:, 3] += boxes[:, 1]
                target['boxes'] = boxes

            targets = filter_and_correct_boxes(targets)
            targets = [{k: v.to(device) for k, v in t.items()} for t in targets]

            outputs = model(images)

            for i, output in enumerate(outputs):
                pred_boxes = output['boxes']
                true_boxes = targets[i]['boxes']
                if pred_boxes.size(0) == 0 or true_boxes.size(0) == 0:
                    continue
                iou = box_iou(pred_boxes, true_boxes)
                iou_list.append(iou.mean().item())

                pred_labels = output['labels']
                true_labels = targets[i]['labels']

                # Ensure both tensors are the same size for comparison
                min_size = min(len(pred_labels), len(true_labels))
                correct_predictions += (pred_labels[:min_size] == true_labels[:min_size]).sum().item()
                total_predictions += min_size

    mean_iou = sum(iou_list) / len(iou_list) if iou_list else 0
    accuracy = correct_predictions / total_predictions if total_predictions else 0
    print(f"Mean IoU: {mean_iou:.4f}, Accuracy: {accuracy:.4f}")
    return mean_iou, accuracy

# Load the test dataset for evaluation
test_dataset = CocoDetection(root='/content/geese-object-detection-dataset/', annFile='/content/geese-object-detection-dataset/validation.json', transform=transform)
test_data_loader = DataLoader(test_dataset, batch_size=2, shuffle=False, num_workers=4, collate_fn=collate_fn)

# Training loop
num_epochs = 10
best_mean_iou = 0

for epoch in range(num_epochs):
    model.train()
    for i, (images, targets) in enumerate(data_loader):
        images = [image.to(device) for image in images]
        targets = [{ 'boxes': torch.tensor([obj['bbox'] for obj in t], dtype=torch.float32).to(device),
                     'labels': torch.tensor([obj['category_id'] for obj in t], dtype=torch.int64).to(device)}
                   for t in targets]
        for target in targets:
            boxes = target['boxes']
            boxes[:, 2] += boxes[:, 0]
            boxes[:, 3] += boxes[:, 1]
            target['boxes'] = boxes

        targets = filter_and_correct_boxes(targets)
        targets = [{k: v.to(device) for k, v in t.items()} for t in targets]

        loss_dict = model(images, targets)
        losses = sum(loss for loss in loss_dict.values())

        optimizer.zero_grad()
        losses.backward()
        optimizer.step()

        if i % 10 == 0:
            print(f"Epoch [{epoch}/{num_epochs}], Step [{i}/{len(data_loader)}], Loss: {losses.item():.4f}")

    lr_scheduler.step()

    mean_iou, accuracy = evaluate_model(model, test_data_loader)
    if mean_iou > best_mean_iou:
        best_mean_iou = mean_iou
        torch.save(model.state_dict(), 'best_model.pth')
        print("Best model saved")

print("Training completed.")

##################################
#%% Load model

# Load the model's state dictionary
model_load_path = '/content/best_model.pth'
model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=False)  # Initialize the model
model.roi_heads.box_predictor = torchvision.models.detection.faster_rcnn.FastRCNNPredictor(in_features, num_classes)  # Adjust the classifier

model.load_state_dict(torch.load(model_load_path, map_location=device))  # Load the state dictionary
model.to(device)  # Move the model to the appropriate device
print(f"Model loaded from {model_load_path}")

###################################
#%% predict on test set

# Define the transformations for the test images
test_transform = T.Compose([T.ToTensor()])

# Load the test dataset
test_dataset = CocoDetection(root='/content/geese-object-detection-dataset/', annFile='/content/geese-object-detection-dataset/test.json', transform=test_transform)

# Use the custom collate function for the test dataset
test_data_loader = DataLoader(test_dataset, batch_size=2, shuffle=False, num_workers=4, collate_fn=collate_fn)

# Function to display an image with its bounding boxes and labels
def show_image_with_predictions(image, predictions):
    fig, ax = plt.subplots(1)
    ax.imshow(image.permute(1, 2, 0))  # Convert image tensor to (H, W, C) format for visualization

    for prediction in predictions:
        bbox = prediction['bbox']
        score = prediction['score']
        label = prediction['label']
        # Create a rectangle patch
        rect = patches.Rectangle((bbox[0], bbox[1]), bbox[2] - bbox[0], bbox[3] - bbox[1], linewidth=1, edgecolor='r', facecolor='none')
        # Add the patch to the Axes
        ax.add_patch(rect)
        plt.text(bbox[0], bbox[1], f"{label}: {score:.2f}", color='white', fontsize=8, bbox=dict(facecolor='red', alpha=0.5))

    plt.show()

# Visualize predictions on the test dataset
model.eval()
with torch.no_grad():
    for images, targets in test_data_loader:
        images = list(image.to(device) for image in images)

        outputs = model(images)

        for i, output in enumerate(outputs):
            image = images[i].cpu()
            predictions = []
            for j in range(len(output['boxes'])):
                bbox = output['boxes'][j].cpu().numpy()
                score = output['scores'][j].cpu().item()
                label = output['labels'][j].cpu().item()
                if score > 0.5:  # Only display predictions with a confidence score above 0.5
                    predictions.append({'bbox': bbox, 'score': score, 'label': label})
            show_image_with_predictions(image, predictions)


# %% Visualize predictions with PIL
##################################

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import torch
from torchvision.transforms import functional as F
from urllib.request import urlopen
import requests
from io import BytesIO


font_url = 'https://github.com/google/fonts/raw/main/apache/robotomono/RobotoMono%5Bwght%5D.ttf'
response = requests.get(font_url)
font = ImageFont.truetype(BytesIO(response.content), size=20)


# Function to draw bounding boxes
def draw_boxes(image, boxes, labels, scores, labels_map):
    draw = ImageDraw.Draw(image, 'RGBA')
    # font = ImageFont.truetype(urlopen(truetype_url), size=20)
    # font = ImageFont.load_default() # default font in pil


    colors = {
        0: (255, 173, 10, 200),  # Class 0 color (e.g., blue)
        1: (28, 140, 252, 200),  # Class 1 color (e.g., orange)
    }
    colors_fill = {
        0: (255, 173, 10, 100),  # Class 0 fill color (e.g., bluea)
        1: (28, 140, 252, 100),  # Class 1 fill color (e.g., orangea)
    }

    for box, label, score in zip(boxes, labels, scores):
        color = colors.get(label, (0, 255, 0, 200))
        fill_color = colors_fill.get(label, (0, 255, 0, 100))
        draw.rectangle([(box[0], box[1]), (box[2], box[3])], outline=color, width=3)
        draw.rectangle([(box[0], box[1]), (box[2], box[3])], fill=fill_color)
        label_text = f"{labels_map[label]}: {score:.2f}"
        text_size = font.getsize(label_text)
        draw.rectangle([(box[0], box[1] - text_size[1]), (box[0] + text_size[0], box[1])], fill=color)
        draw.text((box[0], box[1] - text_size[1]), label_text, fill="white", font=font)

    return image

# Load the model
model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=False)
num_classes = 2  # Example: 1 class (goose) + background
in_features = model.roi_heads.box_predictor.cls_score.in_features
model.roi_heads.box_predictor = torchvision.models.detection.faster_rcnn.FastRCNNPredictor(in_features, num_classes)
model.load_state_dict(torch.load('best_model.pth'))
model.eval()
model.to(device)

# Load a single test image
image_path = '/content/geese-object-detection-dataset/img/01ES4X8G607FY4FP9FV0E3WNNA.png'
image = Image.open(image_path).convert("RGB")
image_tensor = F.to_tensor(image).unsqueeze(0).to(device)

# Run inference
with torch.no_grad():
    outputs = model(image_tensor)

# Get the boxes, labels, and scores
boxes = outputs[0]['boxes'].cpu().numpy()
labels = outputs[0]['labels'].cpu().numpy()
scores = outputs[0]['scores'].cpu().numpy()

# Define labels map
labels_map = {0: "Background", 1: "Goose"}

# Draw the boxes on the image
image_with_boxes = draw_boxes(image, boxes, labels, scores, labels_map)

# Display the image
image_with_boxes.show()

# Save the image
image_with_boxes.save('output_image.jpg')

# %% Visualize predictions on Video 
##################################

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import torch
from torchvision.transforms import functional as F
import requests
from io import BytesIO

# Load font from a URL
font_url = 'https://github.com/google/fonts/raw/main/apache/robotomono/RobotoMono%5Bwght%5D.ttf'
response = requests.get(font_url)
font = ImageFont.truetype(BytesIO(response.content), size=20)

# Function to draw bounding boxes
def draw_boxes(image, boxes, labels, scores, labels_map):
    draw = ImageDraw.Draw(image, 'RGBA')
    colors = {
        0: (255, 173, 10, 200),  # Class 0 color (e.g., blue)
        1: (28, 140, 252, 200),  # Class 1 color (e.g., orange)
    }
    colors_fill = {
        0: (255, 173, 10, 100),  # Class 0 fill color (e.g., bluea)
        1: (28, 140, 252, 100),  # Class 1 fill color (e.g., orangea)
    }

    for box, label, score in zip(boxes, labels, scores):
        color = colors.get(label, (0, 255, 0, 200))
        fill_color = colors_fill.get(label, (0, 255, 0, 100))
        draw.rectangle([(box[0], box[1]), (box[2], box[3])], outline=color, width=3)
        draw.rectangle([(box[0], box[1]), (box[2], box[3])], fill=fill_color)
        label_text = f"{labels_map[label]}: {score:.2f}"
        text_size = font.getsize(label_text)
        draw.rectangle([(box[0], box[1] - text_size[1]), (box[0] + text_size[0], box[1])], fill=color)
        draw.text((box[0], box[1] - text_size[1]), label_text, fill="white", font=font)

    return image

# Load the model
model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=False)
num_classes = 2  # Example: 1 class (goose) + background
in_features = model.roi_heads.box_predictor.cls_score.in_features
model.roi_heads.box_predictor = torchvision.models.detection.faster_rcnn.FastRCNNPredictor(in_features, num_classes)
model.load_state_dict(torch.load('best_model.pth'))
model.eval()
model.to(device)

# Video path and properties
video_path = '/content/test_geesevid2.mp4'
video = cv2.VideoCapture(video_path)
width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
frames_per_second = video.get(cv2.CAP_PROP_FPS)
num_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))

# Initialize video writer
video_writer = cv2.VideoWriter('out.mp4', cv2.VideoWriter_fourcc(*"mp4v"), fps=float(frames_per_second), frameSize=(width, height), isColor=True)

# Function to process video frame by frame
def run_inference_video(video, model, device, labels_map):
    while True:
        hasFrame, frame = video.read()
        if not hasFrame:
            break

        # Convert frame to PIL image
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        image_tensor = F.to_tensor(image).unsqueeze(0).to(device)

        # Run inference
        with torch.no_grad():
            outputs = model(image_tensor)

        # Get the boxes, labels, and scores
        boxes = outputs[0]['boxes'].cpu().numpy()
        labels = outputs[0]['labels'].cpu().numpy()
        scores = outputs[0]['scores'].cpu().numpy()

        # Draw the boxes on the image
        image_with_boxes = draw_boxes(image, boxes, labels, scores, labels_map)

        # Convert back to OpenCV image format
        result_frame = cv2.cvtColor(np.array(image_with_boxes), cv2.COLOR_RGB2BGR)

        yield result_frame

# Run inference and write video
for frame in run_inference_video(video, model, device, labels_map):
    video_writer.write(frame)

# Release resources
video.release()
video_writer.release()
cv2.destroyAllWindows()
