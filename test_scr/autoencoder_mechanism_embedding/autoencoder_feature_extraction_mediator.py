#%% Import the necessary libraries and specify the directories
import numpy as np
import pandas as pd
import os
import tensorflow as tf
from tensorflow.keras import layers, models
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from tensorflow.keras import backend as K
from tensorflow.keras.losses import BinaryCrossentropy
import cv2
mediator_dir = "../../test_data/ICH_data/label_clean/"
#%% Read the mediator images
def img_read(dir_list, size = None):
    img_list = []
    for dir in dir_list:
        img = cv2.imread(dir, cv2.IMREAD_GRAYSCALE)
        if size is not None:
            img = cv2.resize(img, size)
        if img.sum() == 0:
            select_flag = False if np.random.rand() < 0.85 else True
        else:
            select_flag = True
        if select_flag:
            img_list.append(img)
    return np.array(img_list)

label_names = os.listdir(mediator_dir)
img_size= (128,128)
mediator_imgs = img_read([mediator_dir + img_name for img_name in label_names], img_size)
mediator_imgs = mediator_imgs.reshape(mediator_imgs.shape[0], img_size[0], img_size[1], 1)

# %% Split the data into training and testing sets
test_indices = np.random.choice(mediator_imgs.shape[0], int(0.2*mediator_imgs.shape[0]), replace=False)
train_indices = np.setdiff1d(np.arange(mediator_imgs.shape[0]), test_indices)

train_imgs = mediator_imgs[train_indices]
test_imgs = mediator_imgs[test_indices]
#%% data augmentation
non_black_train_imgs = mediator_imgs[mediator_imgs.sum(axis=(1,2,3)) != 0]
datagen = ImageDataGenerator(
    rotation_range=40,
    width_shift_range=0.2,
    height_shift_range=0.2,
    zoom_range=0.2,
    horizontal_flip=True,
    fill_mode='nearest'
)
arg_size = 4
n_augmented_samples = 31
aug_iter = datagen.flow(non_black_train_imgs, batch_size=arg_size, shuffle=True)
augmented_imgs = []

for _ in range(n_augmented_samples):
    for i in range(non_black_train_imgs.shape[0] // arg_size + 1):
        batch = next(aug_iter)
        augmented_imgs.append(batch)
augmented_imgs = np.concatenate(augmented_imgs, axis=0)
train_imgs = np.concatenate((train_imgs, augmented_imgs), axis=0)

# Normalize the images
train_imgs = train_imgs.astype('float32')/ 255.0
test_imgs = test_imgs.astype('float32')/ 255.0
# %% Define the autoencoder structure
# Define the encoder
def build_encoder(input_shape, latent_dim=8):
    input_img = layers.Input(shape=input_shape)
    
    short_cut = input_img
    x = layers.Conv2D(16, (3, 3), activation='relu', padding='same')(input_img)
    x = layers.MaxPooling2D((2, 2), padding='same')(x)
    
    x = layers.Conv2D(32, (3, 3), activation='relu', padding='same')(x)
    x = layers.MaxPooling2D((2, 2), padding='same')(x)
    
    x = layers.Conv2D(64, (2, 2), activation='relu', padding='same')(x)
    x = layers.AveragePooling2D((2, 2), padding='same')(x)
    
    x = layers.Conv2D(128, (2, 2), activation='relu', padding='same')(x)
    x = layers.AveragePooling2D((2, 2), padding='same')(x)
    
    short_cut = layers.AveragePooling2D((16, 16), padding='same')(short_cut)
    x = layers.Add()([x, short_cut])
    x = layers.Activation('relu')(x)
    
    x = layers.Conv2D(64, (1, 1), activation='relu', padding='same')(x)

    map_shape = x.shape
    
    x = layers.Flatten()(x)
    x = layers.Dropout(0.3)(x)
    
    x = layers.Dense(64, activation='relu')(x)
    
    encoded = layers.Dense(latent_dim, activation='tanh')(x)
    
    return models.Model(input_img, encoded), map_shape

# Define the decoder
def build_decoder(encoded_shape, latent_dim=8):
    encoded_input = layers.Input(shape=(latent_dim,))
    
    x = layers.Dense(64, activation='sigmoid')(encoded_input)
    
    x = layers.Dense(np.prod(encoded_shape), activation='relu')(x)
    x = layers.Dropout(0.3)(x)
    
    x = layers.Reshape(encoded_shape)(x)
    
    x = layers.Conv2D(64, (1, 1), activation='relu', padding='same')(x)
    
    short_cut = x
    short_cut = layers.Conv2D(16, (1, 1), activation='relu', padding='same')(short_cut)
    short_cut = layers.UpSampling2D((16, 16))(short_cut)
    
    x = layers.Conv2D(128, (3, 3), activation='relu', padding='same')(x)
    x = layers.Conv2DTranspose(128, (2, 2), activation='relu', strides=(2, 2), padding='same')(x)
    
    x = layers.Conv2D(64, (3, 3), activation='relu', padding='same')(x)
    x = layers.Conv2DTranspose(64, (2, 2), activation='relu', strides=(2, 2), padding='same')(x)
    
    x = layers.Conv2D(32, (3, 3), activation='relu', padding='same')(x)
    x = layers.Conv2DTranspose(32, (3, 3), activation='relu', strides=(2, 2), padding='same')(x)
    
    x = layers.Conv2D(16, (3, 3), activation='relu', padding='same')(x)
    x = layers.Conv2DTranspose(16, (3, 3), activation='relu', strides=(2, 2), padding='same')(x)
    
    x = layers.Add()([x, short_cut])
    x = layers.Activation('relu')(x)
    
    decoded = layers.Conv2D(1, (2, 2), activation='sigmoid', padding='same')(x)
    
    return models.Model(encoded_input, decoded)

# Combine encoder and decoder to create the autoencoder
input_shape = (img_size[0], img_size[1], 1)
encoder, encoder_shape = build_encoder(input_shape, latent_dim=6)
decoder = build_decoder(encoder_shape[1:], latent_dim=6)
autoencoder = models.Model(encoder.input, decoder(encoder.output))

# Define the combined loss function
def combined_loss(y_true, y_pred, alpha=0.5):

    cross_entropy = BinaryCrossentropy()
    ce_loss = cross_entropy(y_true, y_pred)
    
    ssim = 1 - tf.reduce_mean(tf.image.ssim(y_true, y_pred, max_val=1.0))
    
    return alpha * ce_loss + (1 - alpha) * ssim

autoencoder.compile(optimizer='adam', loss= lambda y_true, y_pred: combined_loss(y_true, y_pred, alpha=0.5))
autoencoder.summary()
# %% Train the autoencoder
early_stopping=EarlyStopping(monitor='val_loss', min_delta=0.0002,
                            patience=5, verbose=0, mode='min',
                            baseline=None, restore_best_weights=True, start_from_epoch = 5)

autoencoder.fit(train_imgs, train_imgs, batch_size=8,
                epochs=50, shuffle=True, validation_data=(test_imgs, test_imgs),
                callbacks=[early_stopping])

# %% Extract the embeddings
def img_read_all(dir_list, size = None):
    img_list = []
    for dir in dir_list:
        img = cv2.imread(dir, cv2.IMREAD_GRAYSCALE)
        if size is not None:
            img = cv2.resize(img, size)
            img_list.append(img)
    return np.array(img_list)
img_size= (128,128)
mediator_imgs = img_read_all([mediator_dir + img_name for img_name in label_names], img_size)
mediator_imgs = mediator_imgs.reshape(mediator_imgs.shape[0], img_size[0], img_size[1], 1)
mediator_embedding = encoder.predict(mediator_imgs.astype('float32')/255.0)
#%% Save the embeddings
mediator_embedding_df = pd.DataFrame(mediator_embedding)
mediator_embedding_df.to_csv("mediator_embedding_low.csv", index=False)
